from decimal import Decimal
from typing import NamedTuple, Optional

from cachetools import LRUCache, TTLCache, cached
from loguru import logger
from pancaketrade.utils.config import ConfigSecrets
from pancaketrade.utils.network import fetch_abi
from web3 import Web3
from web3.contract import Contract
from web3.types import ChecksumAddress, Wei
from web3.exceptions import ABIFunctionNotFound


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

    def get_bnb_balance(self) -> Decimal:
        return Decimal(self.w3.eth.get_balance(self.wallet)) / Decimal(10 ** 18)

    def get_token_balance_usd(self, token_address: ChecksumAddress, balance: Optional[Decimal] = None) -> Decimal:
        balance_bnb = self.get_token_balance_bnb(token_address, balance=balance)
        bnb_price = self.get_bnb_price()
        return bnb_price * balance_bnb

    def get_token_balance_bnb(self, token_address: ChecksumAddress, balance: Optional[Decimal] = None) -> Decimal:
        if balance is None:
            balance = self.get_token_balance(token_address=token_address)
        token_price = self.get_token_price(token_address=token_address)
        return token_price * balance

    def get_token_balance(self, token_address: ChecksumAddress) -> Decimal:
        token_contract = self.get_token_contract(token_address)
        try:
            balance = Decimal(token_contract.functions.balanceOf(self.wallet).call()) / Decimal(
                10 ** self.get_token_decimals(token_address=token_address)
            )
        except ABIFunctionNotFound:
            logger.error(f'Contract {token_address} does not have function "balanceOf"')
            return Decimal(0)
        return balance

    def get_bnb_price(self) -> Decimal:
        lp = self.find_lp_address(token_address=self.addr.busd, v2=True)
        if not lp:
            return Decimal(0)
        bnb_amount = Decimal(self.contracts.wbnb.functions.balanceOf(lp).call())
        busd_amount = Decimal(self.contracts.busd.functions.balanceOf(lp).call())
        return busd_amount / bnb_amount

    @cached(cache=TTLCache(maxsize=256, ttl=1))
    def get_token_price(
        self, token_address: ChecksumAddress, token_decimals: Optional[int] = None, sell: bool = True
    ) -> Decimal:
        logger.info(f'Getting price for {token_address}')
        if token_decimals is None:
            token_decimals = self.get_token_decimals(token_address=token_address)
        token_contract = self.get_token_contract(token_address)
        lp_v1 = self.find_lp_address(token_address=token_address, v2=False)
        lp_v2 = self.find_lp_address(token_address=token_address, v2=True)
        if lp_v1 is None and lp_v2 is None:  # no lp
            return Decimal(0)
        elif lp_v2 is None and lp_v1:  # only v1
            return self.get_token_price_by_lp(
                token_contract=token_contract, token_lp=lp_v1, token_decimals=token_decimals
            )
        elif lp_v1 is None and lp_v2:  # only v2
            return self.get_token_price_by_lp(
                token_contract=token_contract, token_lp=lp_v2, token_decimals=token_decimals
            )
        # both exist
        assert lp_v1 and lp_v2
        price_v1 = self.get_token_price_by_lp(
            token_contract=token_contract, token_lp=lp_v1, token_decimals=token_decimals
        )
        price_v2 = self.get_token_price_by_lp(
            token_contract=token_contract, token_lp=lp_v1, token_decimals=token_decimals
        )
        # if the BNB in pool or tokens in pool is zero, we get a price of zero. Also if LP is too empty
        if price_v1 == 0 and price_v2 == 0:  # both lp's are too small, we choose the largest
            return self.get_token_price_by_lp(
                token_contract=token_contract,
                token_lp=self.get_biggest_lp(lp1=lp_v1, lp2=lp_v2),
                token_decimals=token_decimals,
                ignore_poolsize=True,
            )
        elif price_v1 == 0:
            return price_v2
        elif price_v2 == 0:
            return price_v1

        if sell:
            return max(price_v1, price_v2)
        return min(price_v1, price_v2)

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

    @cached(cache=LRUCache(maxsize=256))
    def get_token_decimals(self, token_address: ChecksumAddress) -> int:
        token_contract = self.get_token_contract(token_address=token_address)
        try:
            decimals = token_contract.functions.decimals().call()
        except ABIFunctionNotFound:
            logger.error(f'Contract {token_address} does not have function "decimals"')
            return 0
        return int(decimals)

    @cached(cache=LRUCache(maxsize=256))
    def get_token_symbol(self, token_address: ChecksumAddress) -> str:
        token_contract = self.get_token_contract(token_address=token_address)
        try:
            symbol = token_contract.functions.symbol().call()
        except ABIFunctionNotFound:
            logger.error(f'Contract {token_address} does not have function "symbol"')
            return 'None'
        return symbol

    def get_biggest_lp(self, lp1: ChecksumAddress, lp2: ChecksumAddress) -> ChecksumAddress:
        bnb_amount1 = Decimal(self.contracts.wbnb.functions.balanceOf(lp1).call())
        bnb_amount2 = Decimal(self.contracts.wbnb.functions.balanceOf(lp2).call())
        return lp1 if bnb_amount1 > bnb_amount2 else lp2

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
