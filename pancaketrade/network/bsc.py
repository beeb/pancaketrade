import time
from decimal import Decimal
from typing import NamedTuple, Optional, Tuple

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from cachetools import LRUCache, TTLCache, cached
from loguru import logger
from pancaketrade.utils.config import ConfigSecrets
from pancaketrade.utils.network import fetch_abi
from web3 import Web3
from web3.contract import Contract, ContractFunction
from web3.exceptions import ABIFunctionNotFound, ContractLogicError
from web3.types import ChecksumAddress, HexBytes, Nonce, TxParams, Wei


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

    def __init__(self, addr: NetworkAddresses, w3: Web3, api_key: str) -> None:
        for contract, address in addr._asdict().items():
            setattr(self, contract, w3.eth.contract(address=address, abi=fetch_abi(contract=address, api_key=api_key)))


class Network:
    def __init__(self, rpc: str, wallet: ChecksumAddress, min_pool_size_bnb: float, secrets: ConfigSecrets):
        self.wallet = wallet
        self.min_pool_size_bnb = min_pool_size_bnb
        self.secrets = secrets
        w3_provider = Web3.HTTPProvider(endpoint_uri=rpc)
        self.w3 = Web3(provider=w3_provider)
        self.addr = NetworkAddresses()
        self.contracts = NetworkContracts(addr=self.addr, w3=self.w3, api_key=secrets.bscscan_api_key)
        self.max_approval_hex = f"0x{64 * 'f'}"
        self.max_approval_int = int(self.max_approval_hex, 16)
        self.max_approval_check_hex = f"0x{15 * '0'}{49 * 'f'}"
        self.max_approval_check_int = int(self.max_approval_check_hex, 16)
        self.last_nonce = self.w3.eth.get_transaction_count(self.wallet)
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

    def get_token_balance_usd(self, token_address: ChecksumAddress, balance: Optional[Decimal] = None) -> Decimal:
        balance_bnb = self.get_token_balance_bnb(token_address, balance=balance)
        bnb_price = self.get_bnb_price()
        return bnb_price * balance_bnb

    def get_token_balance_bnb(self, token_address: ChecksumAddress, balance: Optional[Decimal] = None) -> Decimal:
        if balance is None:
            balance = self.get_token_balance(token_address=token_address)
        token_price, _ = self.get_token_price(token_address=token_address)
        return token_price * balance

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

    def get_token_price_usd(
        self, token_address: ChecksumAddress, token_decimals: Optional[int] = None, sell: bool = True
    ) -> Decimal:
        bnb_per_token, _ = self.get_token_price(token_address=token_address, token_decimals=token_decimals, sell=sell)
        usd_per_bnb = self.get_bnb_price()
        return bnb_per_token * usd_per_bnb

    @cached(cache=TTLCache(maxsize=256, ttl=1))
    def get_token_price(
        self, token_address: ChecksumAddress, token_decimals: Optional[int] = None, sell: bool = True
    ) -> Tuple[Decimal, bool]:
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
                    token_contract=token_contract, token_lp=lp_v1, token_decimals=token_decimals
                ),
                False,
            )
        elif lp_v1 is None and lp_v2:  # only v2
            return (
                self.get_token_price_by_lp(
                    token_contract=token_contract, token_lp=lp_v2, token_decimals=token_decimals
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
        self, token_contract: Contract, token_lp: ChecksumAddress, token_decimals: int, ignore_poolsize: bool = False
    ) -> Decimal:
        lp_bnb_amount = Decimal(self.contracts.wbnb.functions.balanceOf(token_lp).call())
        if lp_bnb_amount / Decimal(10 ** 18) < self.min_pool_size_bnb and not ignore_poolsize:  # not enough liquidity
            return Decimal(0)
        lp_token_amount = Decimal(token_contract.functions.balanceOf(token_lp).call()) * Decimal(
            10 ** (18 - token_decimals)
        )
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
        logger.debug(f'Token contract initiated for {token_address}')
        return self.w3.eth.contract(
            address=token_address, abi=fetch_abi(contract=token_address, api_key=self.secrets.bscscan_api_key)
        )

    @cached(cache=TTLCache(maxsize=256, ttl=3600))  # cache 60 minutes
    def find_lp_address(self, token_address: ChecksumAddress, v2: bool = False) -> Optional[ChecksumAddress]:
        contract = self.contracts.factory_v2 if v2 else self.contracts.factory_v1
        pair = contract.functions.getPair(token_address, self.addr.wbnb).call()
        if pair == '0x' + 40 * '0':  # not found
            return None
        return Web3.toChecksumAddress(pair)

    def has_both_versions(self, token_address: ChecksumAddress) -> bool:
        lp_v1 = self.find_lp_address(token_address=token_address, v2=False)
        lp_v2 = self.find_lp_address(token_address=token_address, v2=True)
        return lp_v1 is not None and lp_v2 is not None

    def get_gas_price(self) -> Wei:
        return self.w3.eth.gas_price

    def is_approved(self, token_address: ChecksumAddress, v2: bool = False) -> bool:
        token_contract = self.get_token_contract(token_address=token_address)
        router_address = self.addr.router_v2 if v2 else self.addr.router_v1
        amount = token_contract.functions.allowance(self.wallet, router_address).call()
        return amount >= self.max_approval_check_int

    def approve(self, token_address: ChecksumAddress, v2: bool = False, max_approval: Optional[int] = None) -> bool:
        max_approval = self.max_approval_int if not max_approval else max_approval
        token_contract = self.get_token_contract(token_address=token_address)
        router_address = self.addr.router_v2 if v2 else self.addr.router_v1
        func = token_contract.functions.approve(router_address, max_approval)
        logger.info(f'Approving {self.get_token_symbol(token_address=token_address)} - {token_address}...')
        tx = self.build_and_send_tx(func)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx, timeout=6000)
        if receipt['status'] == 0:  # fail
            logger.error(f'Approval call failed at tx {str(receipt["transactionHash"])}')
            return False
        logger.success('Approved wallet for trading.')
        time.sleep(5)  # let tx propagate
        return True

    def build_and_send_tx(self, func: ContractFunction, tx_params: Optional[TxParams] = None) -> HexBytes:
        if not tx_params:
            tx_params = self.get_tx_params()
        transaction = func.buildTransaction(tx_params)
        signed_tx = self.w3.eth.account.sign_transaction(transaction, private_key=self.secrets._pk)
        try:
            return self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        finally:
            self.last_nonce = Nonce(tx_params["nonce"] + 1)

    def get_tx_params(self, value: Wei = Wei(0), gas: Wei = Wei(50000), gas_price: Optional[Wei] = None) -> TxParams:
        # 50000 gas is OK for approval tx, for other tx use 300000
        params: TxParams = {
            'from': self.wallet,
            'value': value,
            'gas': gas,
            'nonce': self.last_nonce,
        }
        if gas_price:
            params['gasPrice'] = gas_price
        return params
