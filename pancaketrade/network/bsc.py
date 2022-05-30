import time
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from cachetools import LRUCache, TTLCache, cached
from loguru import logger
from requests.auth import HTTPBasicAuth
from web3 import Web3
from web3.contract import Contract, ContractFunction
from web3.exceptions import ABIFunctionNotFound, ContractLogicError
from web3.logs import DISCARD
from web3.middleware import geth_poa_middleware
from web3.types import ChecksumAddress, HexBytes, Nonce, TxParams, TxReceipt, Wei

from pancaketrade.utils.config import ConfigSecrets

GAS_LIMIT_FAILSAFE = Wei(2500000)  # if the estimated limit is above this one, cancel transaction


class NetworkAddresses(NamedTuple):
    wbnb: ChecksumAddress = Web3.toChecksumAddress("0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c")
    busd: ChecksumAddress = Web3.toChecksumAddress("0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56")
    usdt: ChecksumAddress = Web3.toChecksumAddress("0x55d398326f99059ff775485246999027b3197955")
    factory_v1: ChecksumAddress = Web3.toChecksumAddress("0xBCfCcbde45cE874adCB698cC183deBcF17952812")
    factory_v2: ChecksumAddress = Web3.toChecksumAddress("0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73")
    router_v1: ChecksumAddress = Web3.toChecksumAddress("0x05fF2B0DB69458A0750badebc4f9e13aDd608C7F")
    router_v2: ChecksumAddress = Web3.toChecksumAddress("0x10ED43C718714eb63d5aA57B78B54704E256024E")


class NetworkContracts:
    wbnb: Contract
    busd: Contract
    usdt: Contract
    factory_v1: Contract
    factory_v2: Contract
    router_v1: Contract
    router_v2: Contract

    def __init__(self, addr: NetworkAddresses, w3: Web3) -> None:
        for contract, address in addr._asdict().items():
            if "factory" in contract:
                filename = "factory.abi"
            elif "router" in contract:
                filename = "router.abi"
            elif contract == "wbnb":
                filename = "wbnb.abi"
            else:
                filename = "bep20.abi"
            with Path("pancaketrade/abi").joinpath(filename).open("r") as f:
                abi = f.read()
            setattr(self, contract, w3.eth.contract(address=address, abi=abi))


class Network:
    def __init__(
        self,
        rpc: str,
        wallet: ChecksumAddress,
        min_pool_size_bnb: float,
        max_price_impact: float,
        price_in_usd: bool,
        secrets: ConfigSecrets,
    ):
        self.wallet = wallet
        self.min_pool_size_bnb = min_pool_size_bnb
        self.max_price_impact = max_price_impact
        self.price_in_usd = price_in_usd
        self.secrets = secrets
        adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=1)
        session = requests.Session()
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        auth = (
            {"auth": HTTPBasicAuth(secrets.rpc_auth_user, secrets.rpc_auth_password)}
            if secrets.rpc_auth_user and secrets.rpc_auth_password
            else None
        )
        w3_provider = Web3.HTTPProvider(endpoint_uri=rpc, session=session, request_kwargs=auth)
        self.w3 = Web3(provider=w3_provider)
        self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        self.addr = NetworkAddresses()
        self.contracts = NetworkContracts(addr=self.addr, w3=self.w3)
        max_approval_hex = f"0x{64 * 'f'}"
        self.max_approval_int = int(max_approval_hex, 16)
        max_approval_check_hex = f"0x{15 * '0'}{49 * 'f'}"
        self.max_approval_check_int = int(max_approval_check_hex, 16)
        self.last_nonce = self.w3.eth.get_transaction_count(self.wallet)
        self.approved: Set[str] = set()  # token that were already approved
        self.lp_cache: Dict[Tuple[str, str], ChecksumAddress] = {}  # token and base tuples as the key
        self.supported_base_tokens: List[ChecksumAddress] = [self.addr.wbnb, self.addr.busd, self.addr.usdt]
        self.nonce_scheduler = BackgroundScheduler(
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 8}
        )
        self.start_nonce_update()

    def start_nonce_update(self):
        """Add a job to update the account nonce every 10 seconds."""
        trigger = IntervalTrigger(seconds=10)
        self.nonce_scheduler.add_job(self.update_nonce, trigger=trigger)
        self.nonce_scheduler.start()

    def update_nonce(self):
        """Update the stored account nonce if it's higher than the existing cached version."""
        self.last_nonce = max(self.last_nonce, self.w3.eth.get_transaction_count(self.wallet))

    def get_bnb_balance(self) -> Decimal:
        """Get the balance of the account in native coin (BNB).

        Returns:
            Decimal: the balance in BNB units (=ether)
        """
        return Decimal(self.w3.eth.get_balance(self.wallet)) / Decimal(10**18)

    def get_token_balance_usd(
        self, token_address: ChecksumAddress, balance: Optional[Decimal] = None, value: Optional[Decimal] = None
    ) -> Decimal:
        """Get the equivalent value of a token's position in USD.

        If self.price_in_usd is True, then this is equivalent to calling `get_token_balance_value`.

        Args:
            token_address (ChecksumAddress): the address of the token contract
            balance (Optional[Decimal], optional): the wallet's balance for a given token if available. An additional
                request will be made if not available. Defaults to None.
            value (Optional[Decimal], optional): the equivalent value of a token's position in BNB or USD, if available.
                An additional request will be made if not available. Defaults to None.

        Returns:
            Decimal: the value of a token's position in USD
        """
        if value is None:
            value = self.get_token_balance_value(token_address, balance=balance)
        if self.price_in_usd:
            return value
        # value is in BNB, we convert
        bnb_price = self.get_bnb_price()
        return bnb_price * value

    def get_token_balance_value(
        self, token_address: ChecksumAddress, balance: Optional[Decimal] = None, token_price: Optional[Decimal] = None
    ) -> Decimal:
        """Get the value of a token's position in BNB or USD.

        If the token price is given in BNB/token, the result is in BNB. Otherwise it's in USD.

        Args:
            token_address (ChecksumAddress): the address of the token contract
            balance (Optional[Decimal], optional): the wallet's balance for a given token if available. An additional
                request will be made if not available. Defaults to None.
            token_price (Optional[Decimal], optional): the price of the token if available. An additional
                request will be made if not available. Defaults to None.

        Returns:
            Decimal: the value of a token's position in USD or BNB
        """
        if balance is None:
            balance = self.get_token_balance(token_address=token_address)
        if token_price is None:
            token_price, _ = self.get_token_price(token_address=token_address)
        value = token_price * balance  # artifact when balance is zero -> 0e-35
        return Decimal(0) if value < 1e-30 else value

    def get_token_balance(self, token_address: ChecksumAddress) -> Decimal:
        """The size of the user's position for a given token contract.

        Args:
            token_address (ChecksumAddress): address of the token contract

        Returns:
            Decimal: the number of tokens owned by the user's wallet (human-readable, decimal)
        """
        token_contract = self.get_token_contract(token_address)
        try:
            balance = Decimal(token_contract.functions.balanceOf(self.wallet).call()) / Decimal(
                10 ** self.get_token_decimals(token_address=token_address)
            )
        except (ABIFunctionNotFound, ContractLogicError):
            logger.error(f'Contract {token_address} does not have function "balanceOf"')
            return Decimal(0)
        return balance

    @cached(cache=TTLCache(maxsize=256, ttl=0.5))
    def get_token_balance_wei(self, token_address: ChecksumAddress) -> Wei:
        """The size of the user's position for a given token contract, in Wei units.

        Args:
            token_address (ChecksumAddress): address of the token contract

        Returns:
            Wei: the number of tokens owned by the user's wallet, in Wei
        """
        token_contract = self.get_token_contract(token_address)
        try:
            return Wei(token_contract.functions.balanceOf(self.wallet).call())
        except (ABIFunctionNotFound, ContractLogicError):
            logger.error(f'Contract {token_address} does not have function "balanceOf"')
        return Wei(0)

    def get_token_price_usd(self, token_address: ChecksumAddress, token_price: Optional[Decimal] = None) -> Decimal:
        """Get the price for a given token, in USD/token.

        This call is equivalent to `get_token_price` if self.price_in_usd is True.

        Args:
            token_address (ChecksumAddress): address of the token contract
            token_price (Optional[Decimal], optional): the price of the token if available. An additional
                request will be made if not available. Defaults to None.

        Returns:
            Decimal: the token price in USD/token.
        """
        if token_price is None:
            token_price, _ = self.get_token_price(token_address=token_address)  # in USD or BNB depending on config
        if self.price_in_usd:
            return token_price
        usd_per_bnb = self.get_bnb_price()
        return token_price * usd_per_bnb

    @cached(cache=TTLCache(maxsize=256, ttl=1))
    def get_token_price(self, token_address: ChecksumAddress) -> Tuple[Decimal, ChecksumAddress]:
        """Return price of the token in BNB/token or USD/token.

        If self.price_in_usd is True, then price is in USD/token.

        Args:
            token_address (ChecksumAddress): the address of the token

        Returns:
            Tuple[Decimal, ChecksumAddress]: a tuple containing:
                - Decimal: price of the token in BNB or USD
                - ChecksumAddress: the base token of the biggest LP
        """
        if token_address == self.addr.wbnb and self.price_in_usd:  # special case for wbnb
            return self.get_bnb_price(), self.addr.wbnb
        elif token_address == self.addr.wbnb:
            return Decimal(1), self.addr.wbnb
        token = self.get_token_contract(token_address)
        supported_lps = [
            self.find_lp_address(token_address=token_address, base_token_address=base_token_address)
            for base_token_address in self.supported_base_tokens
        ]
        if not [lp for lp in supported_lps if lp is not None]:  # token is not trading yet
            return Decimal(0), self.addr.wbnb
        biggest_lp, lp_index = self.find_biggest_lp(token, lps=supported_lps)
        base_token_address = self.supported_base_tokens[lp_index]
        if biggest_lp is None:
            return Decimal(0), base_token_address
        base_token = self.get_token_contract(base_token_address)
        return self.get_token_price_for_lp(token, base_token, ignore_poolsize=True), base_token_address

    def get_token_price_for_lp(self, token: Contract, base_token: Contract, ignore_poolsize: bool = False) -> Decimal:
        """Return price of the token in BNB/token or USD/token for a given LP defined by its base token.

        The price is given in USD/token if self.price_in_usd is True

        Args:
            token (Contract): token contract instance
            base_token (Contract): base token contract instance
            ignore_poolsize (bool, optional): wether to avoid returning zero when the LP is too small, measured in
                equivalent BNB value staked. The default behavior is to ignore pools that are smaller by returning a
                zero price. Defaults to False.

        Returns:
            Decimal: the price of the token in BNB or USD per token, as calculated from a given pair with the given
            base token.
        """
        lp = self.find_lp_address(token_address=token.address, base_token_address=base_token.address)
        if lp is None:
            return Decimal(0)
        base_decimals = self.get_token_decimals(base_token.address)
        base_amount = Decimal(base_token.functions.balanceOf(lp).call()) * Decimal(
            10 ** (18 - base_decimals)
        )  # e.g. balance of LP for base token, normalized to 18 decimals
        if (
            base_token.address == self.addr.wbnb
            and base_amount / Decimal(10**18) < self.min_pool_size_bnb
            and not ignore_poolsize
        ):
            # Not enough liquidity
            return Decimal(0)
        # If base is not BNB, then base must be dollar-pegged and we divide by the BNB price to find equivalent
        # value in BNB.
        elif (base_amount / self.get_bnb_price()) / Decimal(10**18) < self.min_pool_size_bnb and not ignore_poolsize:
            # Not enough liquidity
            return Decimal(0)

        token_decimals = self.get_token_decimals(token.address)
        token_amount = Decimal(token.functions.balanceOf(lp).call()) * Decimal(10 ** (18 - token_decimals))
        # normalize to 18 decimals
        try:
            base_per_token = base_amount / token_amount
        except Exception:
            base_per_token = Decimal(0)
        value = base_per_token
        if self.price_in_usd:  # we need USD output
            if base_token.address != self.addr.wbnb:  # base is USD
                value = base_per_token  # no change needed
            else:
                value = base_per_token * self.get_bnb_price()  # we convert to USD
        else:  # we need BNB output
            if base_token.address == self.addr.wbnb:  # base is BNB
                value = base_per_token
            else:
                value = base_per_token / self.get_bnb_price()  # we convert to BNB
        return Decimal(0) if value < 1e-30 else value  # artifact with small numbers

    @cached(cache=TTLCache(maxsize=1, ttl=5))
    def _get_base_token_price(self, token: Contract) -> Decimal:
        """Deprecated.

        Get the price in BNB per token for a given base token of some LP.
        This is a simplified version of the token price function that doesn't support non-BNB pairs.

        Args:
            token (Contract): contract instance for the base token

        Returns:
            Decimal: the price in BNB per token for the given base token.
        """
        if token.address == self.addr.wbnb:  # special case for BNB, price is always 1.
            return Decimal(1)
        lp = self.find_lp_address(token.address, self.addr.wbnb)
        if not lp:
            return Decimal(0)
        token_decimals = self.get_token_decimals(token.address)
        bnb_amount = Decimal(self.contracts.wbnb.functions.balanceOf(lp).call())
        token_amount = Decimal(token.functions.balanceOf(lp).call()) * Decimal(10 ** (18 - token_decimals))
        return bnb_amount / token_amount

    @cached(cache=TTLCache(maxsize=1, ttl=30))
    def get_bnb_price(self) -> Decimal:
        """Get the price of the native token in USD/BNB.

        Raises:
            ValueError: if the BNB/BUSD LP can't be found

        Returns:
            Decimal: the price of the chain's native token in USD per BNB.
        """
        lp = self.find_lp_address(token_address=self.addr.busd, base_token_address=self.addr.wbnb)
        if not lp:
            raise ValueError("No LP found for BNB/BUSD")
        bnb_amount = Decimal(self.contracts.wbnb.functions.balanceOf(lp).call())
        busd_amount = Decimal(self.contracts.busd.functions.balanceOf(lp).call())
        return busd_amount / bnb_amount

    def find_biggest_lp(
        self, token: Contract, lps: List[Optional[ChecksumAddress]]
    ) -> Tuple[Optional[ChecksumAddress], int]:
        """Find the largest LP in a list of LP addresses, measured by the amount of tokens staked in it.

        Args:
            token (Contract): token contract instance
            lps (List[Optional[ChecksumAddress]]): list of LP addresses for this token

        Returns:
            Tuple[ChecksumAddress, int]: a tuple containing:
                - ChecksumAddress: the address of the largest LP
                - int: the index of the largest LP in the list provided as input
        """
        lp_balances = [Decimal(token.functions.balanceOf(lp).call()) if lp is not None else Decimal(0) for lp in lps]
        argmax = max(range(len(lp_balances)), key=lambda i: lp_balances[i])
        return lps[argmax], argmax

    def calculate_price_impact(
        self,
        token_address: ChecksumAddress,
        amount_in: Wei,
        sell: bool,
        token_price: Optional[Decimal] = None,
        swap_path: Optional[List] = None,
        amount_out: Optional[Wei] = None,
    ) -> Decimal:
        """Calculate the loss to price impact for a given token (slippage), ignoring the inevitable LP tax.

        Args:
            token_address (ChecksumAddress): token address
            amount_in (Wei): amount to buy/sell
            sell (bool): transaction type (True for sell, False for buy)
            token_price (Optional[Decimal], optional): token price (if already available). Defaults to None.
            swap_path (Optional[List], optional): swap path (if already available). Defaults to None.
            amount_out (Optional[Wei], optional): predicted output amount (if already available). Defaults to None.

        Returns:
            Decimal: the loss to price impact for the given token.
        """
        if token_price is None:
            token_price, _ = self.get_token_price(token_address)
        if self.price_in_usd:  # we need price in BNB / token
            token_price = token_price / self.get_bnb_price()
        quote_amount_out = amount_in * token_price if sell else amount_in / token_price  # with "in" token decimals
        if swap_path is None or amount_out is None:
            swap_path, amount_out = self.get_best_swap_path(token_address, amount_in, sell)  # with "out" token decimals
        quote_amount_out_normalized = quote_amount_out * Decimal(
            10 ** (18 - self.get_token_decimals(swap_path[0]))
        )  # normalize to 18 decimals
        amount_out_normalized = Decimal(amount_out) * Decimal(
            10 ** (18 - self.get_token_decimals(swap_path[-1]))
        )  # normalize to 18 decimals
        slippage = (quote_amount_out_normalized - amount_out_normalized) / quote_amount_out_normalized
        lpFee = Decimal("0.0025") if len(swap_path) == 2 else Decimal("0.0049375")  # 1 - (1-0.25%)^2
        return slippage - lpFee

    def get_best_swap_path(
        self, token_address: ChecksumAddress, amount_in: Wei, sell: bool
    ) -> Tuple[List[ChecksumAddress], Wei]:
        """Find the most advantageous path to swap from a token to BNB, or from BNB to a token.

        The algorithm tries to estimate the direct output from BNB to token swap (or vice-versa), or to first swap to
        another supported token that has a pair for the token of interest, and then swap from that token to BNB/token
        (multihop). The path that gives the largest output will be returned.

        Args:
            token_address (ChecksumAddress): address of the token to buy/sell
            amount_in (Wei): input amount, in Wei, representing either the number of BNB to use for buying, or number
                of tokens to sell.
            sell (bool): wether we are trying to sell tokens (``True``), or buy tokens (``False``).

        Raises:
            ValueError: if one of the tokens in the path doesn't provide a liquidity pool, thus making this path invalid

        Returns:
            Tuple[List[ChecksumAddress], Wei]: a tuple containing:
                - List[ChecksumAddress]: the best path to use for maximum output
                - Wei: the estimated output for the best path (doesn't take into account any token fees, but takes into
                    account the AMM fee)
        """
        if sell:
            paths = [[token_address, self.addr.wbnb]]
            for base_token_address in [bt for bt in self.supported_base_tokens if bt != self.addr.wbnb]:
                paths.append([token_address, base_token_address, self.addr.wbnb])
        else:
            paths = [[self.addr.wbnb, token_address]]
            for base_token_address in [bt for bt in self.supported_base_tokens if bt != self.addr.wbnb]:
                paths.append([self.addr.wbnb, base_token_address, token_address])
        amounts_out: List[Wei] = []
        valid_paths: List[List[ChecksumAddress]] = []
        for path in paths:
            try:
                amount_out = self.contracts.router_v2.functions.getAmountsOut(amount_in, path).call()[-1]
            except ContractLogicError:  # invalid pair
                continue
            amounts_out.append(amount_out)
            valid_paths.append(path)
        if not valid_paths:
            raise ValueError("No valid pair was found")
        argmax = max(range(len(amounts_out)), key=lambda i: amounts_out[i])
        return valid_paths[argmax], amounts_out[argmax]

    def find_lp_address(
        self, token_address: ChecksumAddress, base_token_address: ChecksumAddress
    ) -> Optional[ChecksumAddress]:
        """Get the LP address for a given pair of tokens, if it exists.

        The function will cache its results in case an LP was found, but not cache anything otherwise.

        Args:
            token_address (ChecksumAddress): address of the token to buy/sell
            base_token_address (ChecksumAddress): address of the base token of the pair

        Returns:
            Optional[ChecksumAddress]: the address of the LP if it exists, ``None`` otherwise.
        """
        cached = self.lp_cache.get((str(token_address), str(base_token_address)))
        if cached is not None:
            return cached
        pair = self.contracts.factory_v2.functions.getPair(token_address, base_token_address).call()
        if pair == "0x" + 40 * "0":  # not found, don't cache
            return None
        checksum_pair = Web3.toChecksumAddress(pair)
        self.lp_cache[(str(token_address), str(base_token_address))] = checksum_pair
        return checksum_pair

    def buy_tokens(
        self, token_address: ChecksumAddress, amount_bnb: Wei, slippage_percent: Decimal, gas_price: Optional[str]
    ) -> Tuple[bool, Decimal, str]:
        """Buy tokens with a given amount of BNB, enforcing a maximum slippage, and using the best swap path.

        Args:
            token_address (ChecksumAddress): address of the token to buy
            amount_bnb (Wei): amount of BNB used for buying
            slippage_percent (Decimal): maximum allowable slippage due to token tax, price action and price impact
            gas_price (Optional[str]): optional gas price to use, or use the network's default suggested price if None.

        Returns:
            Tuple[bool, Decimal, str]: a tuple containing:
                - bool: wether the buy was successful
                - Decimal: the amount of tokens received (human-readable, decimal)
                - str: the transaction hash if transaction was mined, or an error message
        """
        balance_bnb = self.w3.eth.get_balance(self.wallet)
        if amount_bnb > balance_bnb - Wei(2000000000000000):  # leave 0.002 BNB for future gas fees
            logger.error("Not enough BNB balance")
            return False, Decimal(0), "Not enough BNB balance"
        slippage_ratio = (Decimal(100) - slippage_percent) / Decimal(100)
        final_gas_price = self.w3.eth.gas_price
        if gas_price is not None and gas_price.startswith("+"):
            offset = Web3.toWei(Decimal(gas_price) * Decimal(10**9), unit="wei")
            final_gas_price = Wei(final_gas_price + offset)
        elif gas_price is not None:
            final_gas_price = Web3.toWei(gas_price, unit="wei")
        try:
            best_path, predicted_out = self.get_best_swap_path(
                token_address=token_address, amount_in=amount_bnb, sell=False
            )
        except ValueError as e:
            logger.error(e)
            return False, Decimal(0), "No compatible LP was found"
        price_impact = self.calculate_price_impact(
            token_address=token_address,
            amount_in=amount_bnb,
            sell=False,
            token_price=None,
            swap_path=best_path,
            amount_out=predicted_out,
        )
        if price_impact > self.max_price_impact:
            logger.error(f"Price impact too high: {price_impact:.2%}")
            return False, Decimal(0), f"Price impact too high at {price_impact:.2%}"
        min_output_tokens = Web3.toWei(slippage_ratio * predicted_out, unit="wei")
        receipt = self.buy_tokens_with_params(
            path=best_path, amount_bnb=amount_bnb, min_output_tokens=min_output_tokens, gas_price=final_gas_price
        )
        if receipt is None:
            logger.error("Can't get gas estimate")
            return (
                False,
                Decimal(0),
                "Can't get gas estimate, or gas estimate too high, check if slippage is set correctly (currently"
                + f" {slippage_percent}%)",
            )
        txhash = Web3.toHex(primitive=receipt["transactionHash"])
        if receipt["status"] == 0:  # fail
            logger.error(f"Buy transaction failed at tx {txhash}")
            return False, Decimal(0), txhash
        amount_out = Decimal(0)
        logs = (
            self.get_token_contract(token_address=token_address)
            .events.Transfer()
            .processReceipt(receipt, errors=DISCARD)
        )
        for log in reversed(logs):  # only get last withdrawal call
            if log["address"] != token_address:
                continue
            if log["args"]["to"] != self.wallet:
                continue
            amount_out = Decimal(log["args"]["value"]) / Decimal(10 ** self.get_token_decimals(token_address))
            break
        logger.success(f"Buy transaction succeeded at tx {txhash}")
        return True, amount_out, txhash

    def buy_tokens_with_params(
        self, path: List[ChecksumAddress], amount_bnb: Wei, min_output_tokens: Wei, gas_price: Wei
    ) -> Optional[TxReceipt]:
        """Craft and submit a transaction to buy tokens through a given swapping path, enforcing a minimum output.

        The function will estimate the gas needed for the transaction and use 120% of that as the gas limit.

        Args:
            path (List[ChecksumAddress]): path to use for swapping (needs to start with WBNB address)
            amount_bnb (Wei): amount of BNB to use for buying, in Wei
            min_output_tokens (Wei): minimum output allowed, in Wei, normally calculated from slippage
            gas_price (Wei): gas price to use, in Wei

        Returns:
            Optional[TxReceipt]: a transaction receipt if transaction was mined, ``None`` otherwise.
        """
        func = self.contracts.router_v2.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
            min_output_tokens, path, self.wallet, self.deadline(60)
        )
        try:
            gas_limit = Wei(int(Decimal(func.estimateGas({"from": self.wallet, "value": amount_bnb})) * Decimal(1.2)))
        except Exception as e:
            logger.error(f"Can't get gas estimate, cancelling transaction: {e}")
            return None
        if gas_limit > GAS_LIMIT_FAILSAFE:
            logger.error("Gas estimate above threshold, cancelling transaction.")
            return None
        params = self.get_tx_params(value=amount_bnb, gas=gas_limit, gas_price=gas_price)
        tx = self.build_and_send_tx(func=func, tx_params=params)
        return self.w3.eth.wait_for_transaction_receipt(tx, timeout=60)

    def sell_tokens(
        self, token_address: ChecksumAddress, amount_tokens: Wei, slippage_percent: Decimal, gas_price: Optional[str]
    ) -> Tuple[bool, Decimal, str]:
        """Sell a given amount of tokens, enforcing a maximum slippage, and using the best swap path.

        Args:
            token_address (ChecksumAddress): token to be sold
            amount_tokens (Wei): amount of tokens to sell, in Wei
            slippage_percent (Decimal): maximum allowable slippage due to token tax, price action and price impact
            gas_price (Optional[str]): optional gas price to use, or use the network's default suggested price if None.

        Returns:
            Tuple[bool, Decimal, str]: a tuple containing:
                - bool: wether the sell was successful
                - Decimal: the amount of BNB received (human-readable, decimal)
                - str: the transaction hash if transaction was mined, or an error message
        """
        balance_tokens = self.get_token_balance_wei(token_address=token_address)
        amount_tokens = min(amount_tokens, balance_tokens)  # partially fill order if possible
        slippage_ratio = (Decimal(100) - slippage_percent) / Decimal(100)
        final_gas_price = self.w3.eth.gas_price
        if gas_price is not None and gas_price.startswith("+"):
            offset = Web3.toWei(Decimal(gas_price) * Decimal(10**9), unit="wei")
            final_gas_price = Wei(final_gas_price + offset)
        elif gas_price is not None:
            final_gas_price = Web3.toWei(gas_price, unit="wei")
        try:
            best_path, predicted_out = self.get_best_swap_path(
                token_address=token_address, amount_in=amount_tokens, sell=True
            )
        except ValueError as e:
            logger.error(e)
            return False, Decimal(0), "No compatible LP was found"
        price_impact = self.calculate_price_impact(
            token_address=token_address,
            amount_in=amount_tokens,
            sell=True,
            token_price=None,
            swap_path=best_path,
            amount_out=predicted_out,
        )
        if price_impact > self.max_price_impact:
            logger.error(f"Price impact too high: {price_impact:.2%}")
            return False, Decimal(0), f"Price impact too high at {price_impact:.2%}"
        min_output_bnb = Web3.toWei(slippage_ratio * predicted_out, unit="wei")
        receipt = self.sell_tokens_with_params(
            path=best_path, amount_tokens=amount_tokens, min_output_bnb=min_output_bnb, gas_price=final_gas_price
        )
        if receipt is None:
            logger.error("Can't get gas estimate")
            return (
                False,
                Decimal(0),
                "Can't get gas estimate, or gas estimate too high, check if slippage is set correctly (currently"
                + f" {slippage_percent}%)",
            )
        txhash = Web3.toHex(primitive=receipt["transactionHash"])
        if receipt["status"] == 0:  # fail
            logger.error(f"Sell transaction failed at tx {txhash}")
            return False, Decimal(0), txhash
        amount_out = Decimal(0)
        logs = self.contracts.wbnb.events.Withdrawal().processReceipt(receipt, errors=DISCARD)
        for log in reversed(logs):  # only get last withdrawal call
            if log["address"] != self.addr.wbnb:
                continue
            if log["args"]["src"] != self.addr.router_v2:
                continue
            amount_out = Decimal(Web3.fromWei(log["args"]["wad"], unit="ether"))
            break
        logger.success(f"Sell transaction succeeded at tx {txhash}")
        return True, amount_out, txhash

    def sell_tokens_with_params(
        self, path: List[ChecksumAddress], amount_tokens: Wei, min_output_bnb: Wei, gas_price: Wei
    ) -> Optional[TxReceipt]:
        """Craft and submit a transaction to sell tokens through a given swapping path, enforcing a minimum output.

        The function will estimate the gas needed for the transaction and use 120% of that as the gas limit.

        Args:
            path (List[ChecksumAddress]): path to use for swapping (needs to start with the token address)
            amount_tokens (Wei): amount of tokens to sell, in Wei
            min_output_bnb (Wei): minimum output allowed, in Wei, normally calculated from slippage
            gas_price (Wei): gas price to use, in Wei

        Returns:
            Optional[TxReceipt]: a transaction receipt if transaction was mined, ``None`` otherwise.
        """
        func = self.contracts.router_v2.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
            amount_tokens, min_output_bnb, path, self.wallet, self.deadline(60)
        )
        try:
            gas_limit = Wei(int(Decimal(func.estimateGas({"from": self.wallet, "value": Wei(0)})) * Decimal(1.2)))
        except Exception as e:
            logger.error(f"Can't get gas estimate, cancelling transaction: {e}")
            return None
        if gas_limit > GAS_LIMIT_FAILSAFE:
            logger.error("Gas estimate above threshold, cancelling transaction.")
            return None
        params = self.get_tx_params(value=Wei(0), gas=gas_limit, gas_price=gas_price)
        tx = self.build_and_send_tx(func=func, tx_params=params)
        return self.w3.eth.wait_for_transaction_receipt(tx, timeout=60)

    @cached(cache=LRUCache(maxsize=256))
    def get_token_decimals(self, token_address: ChecksumAddress) -> int:
        """Get the number of decimals used by the token for human representation.

        Args:
            token_address (ChecksumAddress): the address of the token

        Returns:
            int: the number of decimals
        """
        token_contract = self.get_token_contract(token_address=token_address)
        decimals = token_contract.functions.decimals().call()
        return int(decimals)

    @cached(cache=LRUCache(maxsize=256))
    def get_token_symbol(self, token_address: ChecksumAddress) -> str:
        """Get the symbol for a given token.

        Args:
            token_address (ChecksumAddress): the address of the token

        Returns:
            str: the symbol for that token
        """
        token_contract = self.get_token_contract(token_address=token_address)
        symbol = token_contract.functions.symbol().call()
        return symbol

    @cached(cache=LRUCache(maxsize=256))
    def get_token_contract(self, token_address: ChecksumAddress) -> Contract:
        """Get a contract instance for a given token address.

        Args:
            token_address (ChecksumAddress): address of the token

        Returns:
            Contract: a web3 contract instance that can be used to perform calls and transactions
        """
        with Path("pancaketrade/abi/bep20.abi").open("r") as f:
            abi = f.read()
        return self.w3.eth.contract(address=token_address, abi=abi)

    def is_approved(self, token_address: ChecksumAddress) -> bool:
        """Check wether the pancakeswap router is allowed to spend a given token.

        Args:
            token_address (ChecksumAddress): the token address

        Returns:
            bool: wether the token was approved
        """
        if str(token_address) in self.approved:
            return True
        token_contract = self.get_token_contract(token_address=token_address)
        amount = token_contract.functions.allowance(self.wallet, self.addr.router_v2).call()
        approved = amount >= self.max_approval_check_int
        if approved:
            self.approved.add(str(token_address))
        return approved

    def approve(self, token_address: ChecksumAddress, max_approval: Optional[int] = None) -> bool:
        """Set the allowance of the pancakeswap router to spend a given token.

        Args:
            token_address (ChecksumAddress): the token to approve
            max_approval (Optional[int], optional): an optional maximum amount to give as allowance. Will use the
                maximum uint256 bound (0xffff....) if set to ``None``. Defaults to None.

        Returns:
            bool: wether the approval transaction succeeded
        """
        max_approval = self.max_approval_int if not max_approval else max_approval
        token_contract = self.get_token_contract(token_address=token_address)
        func = token_contract.functions.approve(self.addr.router_v2, max_approval)
        logger.info(f"Approving {self.get_token_symbol(token_address=token_address)} - {token_address}...")
        try:
            gas_limit = Wei(int(Decimal(func.estimateGas({"from": self.wallet, "value": Wei(0)})) * Decimal(1.2)))
        except Exception:
            gas_limit = Wei(100000)
        tx_params = self.get_tx_params(
            gas=gas_limit,
            gas_price=Wei(self.w3.eth.gas_price + Web3.toWei(Decimal("0.1") * Decimal(10**9), unit="wei")),
        )
        tx = self.build_and_send_tx(func, tx_params=tx_params)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx, timeout=6000)
        if receipt["status"] == 0:  # fail
            logger.error(f'Approval call failed at tx {Web3.toHex(primitive=receipt["transactionHash"])}')
            return False
        self.approved.add(str(token_address))
        time.sleep(3)  # let tx propagate
        logger.success("Approved wallet for trading.")
        return True

    def get_gas_price(self) -> Wei:
        """Get the network's suggested gas price in Wei.

        Returns:
            Wei: the network default gas price
        """
        return self.w3.eth.gas_price

    def build_and_send_tx(self, func: ContractFunction, tx_params: Optional[TxParams] = None) -> HexBytes:
        """Build a transaction from a contract's function call instance and transaction parameters, then submit it.

        Args:
            func (ContractFunction): a function call instance from a contract
            tx_params (Optional[TxParams], optional): optional transaction parameters. Defaults to None.

        Returns:
            HexBytes: the transaction hash
        """
        if not tx_params:
            tx_params = self.get_tx_params()
        transaction = func.buildTransaction(tx_params)
        signed_tx = self.w3.eth.account.sign_transaction(transaction, private_key=self.secrets._pk)
        try:
            return self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        finally:
            self.last_nonce = Nonce(tx_params["nonce"] + 1)

    def get_tx_params(
        self, value: Optional[Wei] = None, gas: Optional[Wei] = None, gas_price: Optional[Wei] = None
    ) -> TxParams:
        """Build a transaction parameters dictionary from the provied parameters.

        The default gas limit of 100k is enough for a normal approval transaction.

        Args:
            value (Optional[Wei], optional): value (BNB) of the transaction, in Wei. Defaults to None which is zero.
            gas (Optional[Wei], optional): gas limit to use, in Wei. Defaults to None which is 100000.
            gas_price (Optional[Wei], optional): gas price to use, in Wei, or None for network default. Defaults to
                None.

        Returns:
            TxParams: a transaction parameters dictionary
        """
        if value is None:
            value = Wei(0)
        if gas is None:
            gas = Wei(100000)
        nonce = max(self.last_nonce, self.w3.eth.get_transaction_count(self.wallet))
        params: TxParams = {"from": self.wallet, "value": value, "gas": gas, "nonce": nonce}
        if gas_price:
            params["gasPrice"] = gas_price
        return params

    def deadline(self, seconds: int = 60) -> int:
        """Get the unix timestamp for a point in time x seconds in the future.

        Args:
            seconds (int, optional): how many seconds in the future. Defaults to 60.

        Returns:
            int: a unix timestamp x seconds in the future
        """
        return int(time.time()) + seconds
