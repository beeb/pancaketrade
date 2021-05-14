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
    def __init__(self, rpc: str, wallet: ChecksumAddress, secrets: ConfigSecrets):
        self.wallet = wallet
        self.secrets = secrets
        w3_provider = Web3.HTTPProvider(endpoint_uri=rpc)
        self.w3 = Web3(provider=w3_provider)
        self.addr = NetworkAddresses()
        self.contracts = NetworkContracts(addr=self.addr, w3=self.w3, api_key=secrets.bscscan_api_key)

    def get_bnb_balance(self) -> Decimal:
        return Decimal(self.w3.eth.get_balance(self.wallet)) / Decimal(10 ** 18)

    def get_bnb_price(self) -> Decimal:
        lp = self.find_lp_address(token_address=self.addr.busd, v2=True)
        if not lp:
            return Decimal(0)
        bnb_amount = Decimal(self.contracts.wbnb.functions.balanceOf(lp).call())
        busd_amount = Decimal(self.contracts.busd.functions.balanceOf(lp).call())
        return busd_amount / bnb_amount

    @cached(cache=TTLCache(maxsize=64, ttl=30))  # cache 30 seconds
    def get_current_balance(self, token_address: ChecksumAddress) -> Decimal:
        token_contract = self.get_token_contract(token_address)
        try:
            balance = Decimal(token_contract.functions.balanceOf(self.wallet).call()) / Decimal(
                10 ** self.get_token_decimals(token_address)
            )
        except ABIFunctionNotFound:
            logger.error(f'Contract {token_address} does not have function "balanceOf"')
            return Decimal(0)
        return balance

    @cached(cache=LRUCache(maxsize=256))
    def get_token_contract(self, token_address: ChecksumAddress) -> Contract:
        logger.debug(f'Token contract initiated for {token_address}')
        return self.w3.eth.contract(
            address=token_address, abi=fetch_abi(contract=token_address, api_key=self.secrets.bscscan_api_key)
        )

    @cached(cache=LRUCache(maxsize=256))
    def get_token_decimals(self, token_address: ChecksumAddress) -> int:
        token_contract = self.w3.eth.contract(
            address=token_address, abi=fetch_abi(contract=token_address, api_key=self.secrets.bscscan_api_key)
        )
        try:
            decimals = token_contract.functions.decimals().call()
        except ABIFunctionNotFound:
            logger.error(f'Contract {token_address} does not have function "decimals"')
            return 0
        return int(decimals)

    @cached(cache=LRUCache(maxsize=256))
    def get_token_symbol(self, token_address: ChecksumAddress) -> str:
        token_contract = self.w3.eth.contract(
            address=token_address, abi=fetch_abi(contract=token_address, api_key=self.secrets.bscscan_api_key)
        )
        try:
            symbol = token_contract.functions.symbol().call()
        except ABIFunctionNotFound:
            logger.error(f'Contract {token_address} does not have function "symbol"')
            return 'None'
        return symbol

    @cached(cache=TTLCache(maxsize=256, ttl=3600))  # cache 60 minutes
    def find_lp_address(self, token_address: ChecksumAddress, v2: bool = False) -> Optional[str]:
        contract = self.contracts.factory_v2 if v2 else self.contracts.factory_v1
        pair = contract.functions.getPair(token_address, self.addr.wbnb).call()
        if pair == '0x' + 40 * '0':  # not found
            return None
        return pair

    def get_gas_price(self) -> Wei:
        return self.w3.eth.gas_price
