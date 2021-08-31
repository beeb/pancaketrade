import time
from decimal import Decimal
from pathlib import Path
from typing import Dict, NamedTuple, Optional, Set, Tuple

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from cachetools import LRUCache, TTLCache, cached
from loguru import logger
from pancaketrade.utils.config import ConfigSecrets
from requests.auth import HTTPBasicAuth
from web3 import Web3
from web3.contract import Contract, ContractFunction
from web3.exceptions import ABIFunctionNotFound, ContractLogicError
from web3.middleware import geth_poa_middleware
from web3.types import BlockIdentifier, ChecksumAddress, HexBytes, Nonce, TxParams, TxReceipt, Wei

GAS_LIMIT_FAILSAFE = Wei(2000000)  # if the estimated limit is above this one, don't use the estimated price


class NetworkAddresses(NamedTuple):
    wbnb: ChecksumAddress = Web3.toChecksumAddress('0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c')
    busd: ChecksumAddress = Web3.toChecksumAddress('0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56')
    factory_v1: ChecksumAddress = Web3.toChecksumAddress('0xBCfCcbde45cE874adCB698cC183deBcF17952812')
    factory_v2: ChecksumAddress = Web3.toChecksumAddress('0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73')
    router_v1: ChecksumAddress = Web3.toChecksumAddress('0x05fF2B0DB69458A0750badebc4f9e13aDd608C7F')
    router_v2: ChecksumAddress = Web3.toChecksumAddress('0x10ED43C718714eb63d5aA57B78B54704E256024E')


class NetworkContracts:
    wbnb: Contract
    busd: Contract
    factory_v1: Contract
    factory_v2: Contract
    router_v1: Contract
    router_v2: Contract

    def __init__(self, addr: NetworkAddresses, w3: Web3) -> None:
        for contract, address in addr._asdict().items():
            if 'factory' in contract:
                filename = 'factory.abi'
            elif 'router' in contract:
                filename = 'router.abi'
            elif contract == 'wbnb':
                filename = 'wbnb.abi'
            else:
                filename = 'bep20.abi'
            with Path('pancaketrade/abi').joinpath(filename).open('r') as f:
                abi = f.read()
            setattr(self, contract, w3.eth.contract(address=address, abi=abi))


class Network:
    def __init__(self, rpc: str, wallet: ChecksumAddress, min_pool_size_bnb: float, secrets: ConfigSecrets):
        self.wallet = wallet
        self.min_pool_size_bnb = min_pool_size_bnb
        self.secrets = secrets
        adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=1)
        session = requests.Session()
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        auth = (
            {'auth': HTTPBasicAuth(secrets.rpc_auth_user, secrets.rpc_auth_password)}
            if secrets.rpc_auth_user and secrets.rpc_auth_password
            else None
        )
        w3_provider = Web3.HTTPProvider(endpoint_uri=rpc, session=session, request_kwargs=auth)
        self.w3 = Web3(provider=w3_provider)
        self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        self.addr = NetworkAddresses()
        self.contracts = NetworkContracts(addr=self.addr, w3=self.w3)
        self.max_approval_hex = f"0x{64 * 'f'}"
        self.max_approval_int = int(self.max_approval_hex, 16)
        self.max_approval_check_hex = f"0x{15 * '0'}{49 * 'f'}"
        self.max_approval_check_int = int(self.max_approval_check_hex, 16)
        self.last_nonce = self.w3.eth.get_transaction_count(self.wallet)
        self.approved: Set[Tuple[str, bool]] = set()  # address and v2 boolean tuples
        self.lp_cache: Dict[Tuple[str, bool], ChecksumAddress] = {}  # address and v2 boolean tuples as the key
        self.nonce_scheduler = BackgroundScheduler(
            job_defaults={
                'coalesce': True,
                'max_instances': 1,
                'misfire_grace_time': 8,
            }
        )
        self.start_nonce_update()

    def start_nonce_update(self):
        trigger = IntervalTrigger(seconds=10)
        self.nonce_scheduler.add_job(self.update_nonce, trigger=trigger)
        self.nonce_scheduler.start()

    def update_nonce(self):
        self.last_nonce = max(self.last_nonce, self.w3.eth.get_transaction_count(self.wallet))

    def get_bnb_balance(self) -> Decimal:
        return Decimal(self.w3.eth.get_balance(self.wallet)) / Decimal(10 ** 18)

    def get_token_balance_usd(
        self, token_address: ChecksumAddress, balance: Optional[Decimal] = None, balance_bnb: Optional[Decimal] = None
    ) -> Decimal:
        if balance_bnb is None:
            balance_bnb = self.get_token_balance_bnb(token_address, balance=balance)
        bnb_price = self.get_bnb_price()
        return bnb_price * balance_bnb

    def get_token_balance_bnb(
        self, token_address: ChecksumAddress, balance: Optional[Decimal] = None, token_price: Optional[Decimal] = None
    ) -> Decimal:
        if balance is None:
            balance = self.get_token_balance(token_address=token_address)
        if token_price is None:
            token_price, _ = self.get_token_price(token_address=token_address)
        bal_bnb = token_price * balance  # artifact when balance is zero -> 0e-35
        return Decimal(0) if bal_bnb < 1e-30 else bal_bnb

    def get_token_balance(self, token_address: ChecksumAddress) -> Decimal:
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
        token_contract = self.get_token_contract(token_address)
        try:
            return Wei(token_contract.functions.balanceOf(self.wallet).call())
        except (ABIFunctionNotFound, ContractLogicError):
            logger.error(f'Contract {token_address} does not have function "balanceOf"')
        return Wei(0)

    def get_token_price_usd(
        self,
        token_address: ChecksumAddress,
        token_decimals: Optional[int] = None,
        sell: bool = True,
        token_price: Optional[Decimal] = None,
    ) -> Decimal:
        if token_price is None:
            token_price, _ = self.get_token_price(token_address=token_address, token_decimals=token_decimals, sell=sell)
        usd_per_bnb = self.get_bnb_price()
        return token_price * usd_per_bnb

    @cached(cache=TTLCache(maxsize=256, ttl=1))
    def get_token_price(
        self, token_address: ChecksumAddress, token_decimals: Optional[int] = None, sell: bool = True
    ) -> Tuple[Decimal, bool]:
        if token_address == self.addr.wbnb:  # special case for wbnb
            return Decimal(1), True
        if token_decimals is None:
            token_decimals = self.get_token_decimals(token_address=token_address)
        token_contract = self.get_token_contract(token_address)
        lp_v1 = self.find_lp_address(token_address=token_address, v2=False)
        lp_v2 = self.find_lp_address(token_address=token_address, v2=True)
        if lp_v1 is None and lp_v2 is None:  # no lp
            return Decimal(0), True
        elif lp_v2 is None and lp_v1:  # only v1
            return (
                self.get_token_price_by_lp(
                    token_contract=token_contract, token_lp=lp_v1, token_decimals=token_decimals, ignore_poolsize=True
                ),
                False,
            )
        elif lp_v1 is None and lp_v2:  # only v2
            return (
                self.get_token_price_by_lp(
                    token_contract=token_contract, token_lp=lp_v2, token_decimals=token_decimals, ignore_poolsize=True
                ),
                True,
            )
        # both exist
        assert lp_v1 and lp_v2
        price_v1 = self.get_token_price_by_lp(
            token_contract=token_contract, token_lp=lp_v1, token_decimals=token_decimals
        )
        price_v2 = self.get_token_price_by_lp(
            token_contract=token_contract, token_lp=lp_v2, token_decimals=token_decimals
        )
        # if the BNB in pool or tokens in pool is zero, we get a price of zero. Also if LP is too empty
        if price_v1 == 0 and price_v2 == 0:  # both lp's are too small, we choose the largest
            biggest_lp, v2 = self.get_biggest_lp(lp1=lp_v1, lp2=lp_v2)
            return (
                self.get_token_price_by_lp(
                    token_contract=token_contract,
                    token_lp=biggest_lp,
                    token_decimals=token_decimals,
                    ignore_poolsize=True,
                ),
                v2,
            )
        elif price_v1 == 0:
            return price_v2, True
        elif price_v2 == 0:
            return price_v1, False

        if sell:
            return max(price_v1, price_v2), price_v2 > price_v1
        return min(price_v1, price_v2), price_v2 < price_v1

    def get_token_price_by_lp(
        self,
        token_contract: Contract,
        token_lp: ChecksumAddress,
        token_decimals: int,
        ignore_poolsize: bool = False,
        block_identifier: BlockIdentifier = 'latest',
    ) -> Decimal:
        lp_bnb_amount = Decimal(
            self.contracts.wbnb.functions.balanceOf(token_lp).call(block_identifier=block_identifier)
        )
        if lp_bnb_amount / Decimal(10 ** 18) < self.min_pool_size_bnb and not ignore_poolsize:  # not enough liquidity
            return Decimal(0)
        lp_token_amount = Decimal(
            token_contract.functions.balanceOf(token_lp).call(block_identifier=block_identifier)
        ) * Decimal(10 ** (18 - token_decimals))
        # normalize to 18 decimals
        try:
            bnb_per_token = lp_bnb_amount / lp_token_amount
        except Exception:
            bnb_per_token = Decimal(0)
        return bnb_per_token

    @cached(cache=TTLCache(maxsize=1, ttl=30))
    def get_bnb_price(self) -> Decimal:
        lp = self.find_lp_address(token_address=self.addr.busd, v2=True)
        if not lp:
            return Decimal(0)
        bnb_amount = Decimal(self.contracts.wbnb.functions.balanceOf(lp).call())
        busd_amount = Decimal(self.contracts.busd.functions.balanceOf(lp).call())
        return busd_amount / bnb_amount

    @cached(cache=LRUCache(maxsize=256))
    def get_token_decimals(self, token_address: ChecksumAddress) -> int:
        token_contract = self.get_token_contract(token_address=token_address)
        decimals = token_contract.functions.decimals().call()
        return int(decimals)

    @cached(cache=LRUCache(maxsize=256))
    def get_token_symbol(self, token_address: ChecksumAddress) -> str:
        token_contract = self.get_token_contract(token_address=token_address)
        symbol = token_contract.functions.symbol().call()
        return symbol

    def get_biggest_lp(self, lp1: ChecksumAddress, lp2: ChecksumAddress) -> Tuple[ChecksumAddress, bool]:
        bnb_amount1 = Decimal(self.contracts.wbnb.functions.balanceOf(lp1).call())
        bnb_amount2 = Decimal(self.contracts.wbnb.functions.balanceOf(lp2).call())
        if bnb_amount1 > bnb_amount2:
            return lp1, False
        return lp2, True

    @cached(cache=LRUCache(maxsize=256))
    def get_token_contract(self, token_address: ChecksumAddress) -> Contract:
        with Path('pancaketrade/abi/bep20.abi').open('r') as f:
            abi = f.read()
        return self.w3.eth.contract(address=token_address, abi=abi)

    def find_lp_address(self, token_address: ChecksumAddress, v2: bool = False) -> Optional[ChecksumAddress]:
        cached = self.lp_cache.get((str(token_address), v2))
        if cached is not None:
            return cached
        contract = self.contracts.factory_v2 if v2 else self.contracts.factory_v1
        pair = contract.functions.getPair(token_address, self.addr.wbnb).call()
        if pair == '0x' + 40 * '0':  # not found, don't cache
            return None
        checksum_pair = Web3.toChecksumAddress(pair)
        self.lp_cache[(str(token_address), v2)] = checksum_pair
        return checksum_pair

    def has_both_versions(self, token_address: ChecksumAddress) -> bool:
        lp_v1 = self.find_lp_address(token_address=token_address, v2=False)
        lp_v2 = self.find_lp_address(token_address=token_address, v2=True)
        return lp_v1 is not None and lp_v2 is not None

    def get_gas_price(self) -> Wei:
        return self.w3.eth.gas_price

    def is_approved(self, token_address: ChecksumAddress, v2: bool = False) -> bool:
        if (str(token_address), v2) in self.approved:
            return True
        token_contract = self.get_token_contract(token_address=token_address)
        router_address = self.addr.router_v2 if v2 else self.addr.router_v1
        amount = token_contract.functions.allowance(self.wallet, router_address).call()
        approved = amount >= self.max_approval_check_int
        if approved:
            self.approved.add((str(token_address), v2))
        return approved

    def approve(self, token_address: ChecksumAddress, v2: bool = False, max_approval: Optional[int] = None) -> bool:
        max_approval = self.max_approval_int if not max_approval else max_approval
        token_contract = self.get_token_contract(token_address=token_address)
        router_address = self.addr.router_v2 if v2 else self.addr.router_v1
        func = token_contract.functions.approve(router_address, max_approval)
        logger.info(f'Approving {self.get_token_symbol(token_address=token_address)} - {token_address}...')
        try:
            gas_limit = Wei(int(Decimal(func.estimateGas({'from': self.wallet, 'value': Wei(0)})) * Decimal(1.2)))
        except Exception:
            gas_limit = Wei(100000)
        tx_params = self.get_tx_params(
            gas=gas_limit,
            gas_price=Wei(self.w3.eth.gas_price + Web3.toWei(Decimal('0.1') * Decimal(10 ** 9), unit='wei')),
        )
        tx = self.build_and_send_tx(func, tx_params=tx_params)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx, timeout=6000)
        if receipt['status'] == 0:  # fail
            logger.error(f'Approval call failed at tx {Web3.toHex(primitive=receipt["transactionHash"])}')
            return False
        self.approved.add((str(token_address), v2))
        time.sleep(3)  # let tx propagate
        logger.success('Approved wallet for trading.')
        return True

    def buy_tokens(
        self,
        token_address: ChecksumAddress,
        amount_bnb: Wei,
        slippage_percent: Decimal,
        gas_price: Optional[str],
        v2: bool = True,
    ) -> Tuple[bool, Decimal, str]:
        balance_bnb = self.w3.eth.get_balance(self.wallet)
        if amount_bnb > balance_bnb - Wei(2000000000000000):  # leave 0.002 BNB for future gas fees
            logger.error('Not enough BNB balance')
            return False, Decimal(0), 'Not enough BNB balance'
        slippage_ratio = (Decimal(100) - slippage_percent) / Decimal(100)
        final_gas_price = self.w3.eth.gas_price
        if gas_price is not None and gas_price.startswith('+'):
            offset = Web3.toWei(Decimal(gas_price) * Decimal(10 ** 9), unit='wei')
            final_gas_price = Wei(final_gas_price + offset)
        elif gas_price is not None:
            final_gas_price = Web3.toWei(gas_price, unit='wei')
        router_contract = self.contracts.router_v2 if v2 else self.contracts.router_v1
        predicted_out = router_contract.functions.getAmountsOut(amount_bnb, [self.addr.wbnb, token_address]).call()[-1]
        min_output_tokens = Web3.toWei(slippage_ratio * predicted_out, unit='wei')
        receipt = self.buy_tokens_with_params(
            token_address=token_address,
            amount_bnb=amount_bnb,
            min_output_tokens=min_output_tokens,
            gas_price=final_gas_price,
            v2=v2,
        )
        if receipt is None:
            logger.error('Can\'t get gas estimate')
            return (
                False,
                Decimal(0),
                f'Can\'t get gas estimate, check if slippage is set correctly (currently {slippage_percent}%)',
            )
        txhash = Web3.toHex(primitive=receipt["transactionHash"])
        if receipt['status'] == 0:  # fail
            logger.error(f'Buy transaction failed at tx {txhash}')
            return False, Decimal(0), txhash
        amount_out = Decimal(0)
        logs = self.get_token_contract(token_address=token_address).events.Transfer().processReceipt(receipt)
        for log in reversed(logs):  # only get last withdrawal call
            if log['address'] != token_address:
                continue
            if log['args']['to'] != self.wallet:
                continue
            amount_out = Decimal(log['args']['value']) / Decimal(10 ** self.get_token_decimals(token_address))
            break
        logger.success(f'Buy transaction succeeded at tx {txhash}')
        return True, amount_out, txhash

    def buy_tokens_with_params(
        self,
        token_address: ChecksumAddress,
        amount_bnb: Wei,
        min_output_tokens: Wei,
        gas_price: Wei,
        v2: bool,
    ) -> Optional[TxReceipt]:
        router_contract = self.contracts.router_v2 if v2 else self.contracts.router_v1
        func = router_contract.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
            min_output_tokens, [self.addr.wbnb, token_address], self.wallet, self.deadline(60)
        )
        try:
            gas_limit = Wei(int(Decimal(func.estimateGas({'from': self.wallet, 'value': amount_bnb})) * Decimal(1.2)))
        except Exception as e:
            logger.error(f'Can\'t get gas estimate, cancelling transaction: {e}')
            return None
        if gas_limit > GAS_LIMIT_FAILSAFE:
            gas_limit = GAS_LIMIT_FAILSAFE
        params = self.get_tx_params(value=amount_bnb, gas=gas_limit, gas_price=gas_price)
        tx = self.build_and_send_tx(func=func, tx_params=params)
        return self.w3.eth.wait_for_transaction_receipt(tx, timeout=60)

    def sell_tokens(
        self,
        token_address: ChecksumAddress,
        amount_tokens: Wei,
        slippage_percent: Decimal,
        gas_price: Optional[str],
        v2: bool = True,
    ) -> Tuple[bool, Decimal, str]:
        balance_tokens = self.get_token_balance_wei(token_address=token_address)
        amount_tokens = min(amount_tokens, balance_tokens)  # partially fill order if possible
        slippage_ratio = (Decimal(100) - slippage_percent) / Decimal(100)
        final_gas_price = self.w3.eth.gas_price
        if gas_price is not None and gas_price.startswith('+'):
            offset = Web3.toWei(Decimal(gas_price) * Decimal(10 ** 9), unit='wei')
            final_gas_price = Wei(final_gas_price + offset)
        elif gas_price is not None:
            final_gas_price = Web3.toWei(gas_price, unit='wei')
        router_contract = self.contracts.router_v2 if v2 else self.contracts.router_v1
        predicted_out = router_contract.functions.getAmountsOut(amount_tokens, [token_address, self.addr.wbnb]).call()[
            -1
        ]
        min_output_bnb = Web3.toWei(slippage_ratio * predicted_out, unit='wei')
        receipt = self.sell_tokens_with_params(
            token_address=token_address,
            amount_tokens=amount_tokens,
            min_output_bnb=min_output_bnb,
            gas_price=final_gas_price,
            v2=v2,
        )
        if receipt is None:
            logger.error('Can\'t get gas estimate')
            return (
                False,
                Decimal(0),
                f'Can\'t get gas estimate, check if slippage is set correctly (currently {slippage_percent}%)',
            )
        txhash = Web3.toHex(primitive=receipt["transactionHash"])
        if receipt['status'] == 0:  # fail
            logger.error(f'Sell transaction failed at tx {txhash}')
            return False, Decimal(0), txhash
        amount_out = Decimal(0)
        logs = self.contracts.wbnb.events.Withdrawal().processReceipt(receipt)
        for log in reversed(logs):  # only get last withdrawal call
            if log['address'] != self.addr.wbnb:
                continue
            if log['args']['src'] != router_contract.address:
                continue
            amount_out = Decimal(Web3.fromWei(log['args']['wad'], unit='ether'))
            break
        logger.success(f'Sell transaction succeeded at tx {txhash}')
        return True, amount_out, txhash

    def sell_tokens_with_params(
        self,
        token_address: ChecksumAddress,
        amount_tokens: Wei,
        min_output_bnb: Wei,
        gas_price: Wei,
        v2: bool,
    ) -> Optional[TxReceipt]:
        router_contract = self.contracts.router_v2 if v2 else self.contracts.router_v1
        func = router_contract.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
            amount_tokens, min_output_bnb, [token_address, self.addr.wbnb], self.wallet, self.deadline(60)
        )
        try:
            gas_limit = Wei(int(Decimal(func.estimateGas({'from': self.wallet, 'value': Wei(0)})) * Decimal(1.2)))
        except Exception as e:
            logger.error(f'Can\'t get gas estimate, cancelling transaction: {e}')
            return None
        if gas_limit > GAS_LIMIT_FAILSAFE:
            gas_limit = GAS_LIMIT_FAILSAFE
        params = self.get_tx_params(value=Wei(0), gas=gas_limit, gas_price=gas_price)
        tx = self.build_and_send_tx(func=func, tx_params=params)
        return self.w3.eth.wait_for_transaction_receipt(tx, timeout=60)

    def build_and_send_tx(self, func: ContractFunction, tx_params: Optional[TxParams] = None) -> HexBytes:
        if not tx_params:
            tx_params = self.get_tx_params()
        transaction = func.buildTransaction(tx_params)
        signed_tx = self.w3.eth.account.sign_transaction(transaction, private_key=self.secrets._pk)
        try:
            return self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        finally:
            self.last_nonce = Nonce(tx_params["nonce"] + 1)

    def get_tx_params(self, value: Wei = Wei(0), gas: Wei = Wei(100000), gas_price: Optional[Wei] = None) -> TxParams:
        # 100000 gas is OK for approval tx, so it's the default
        nonce = max(self.last_nonce, self.w3.eth.get_transaction_count(self.wallet))
        params: TxParams = {
            'from': self.wallet,
            'value': value,
            'gas': gas,
            'nonce': nonce,
        }
        if gas_price:
            params['gasPrice'] = gas_price
        return params

    def deadline(self, seconds: int = 60) -> int:
        return int(time.time()) + seconds
