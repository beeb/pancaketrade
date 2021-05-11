from typing import NamedTuple

from cachetools import LRUCache, cached
from pancaketrade.utils.config import ConfigSecrets
from pancaketrade.utils.web3 import fetch_abi
from web3 import Web3
from web3.contract import Contract
from web3.types import ChecksumAddress, Wei


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
    def __init__(self, secrets: ConfigSecrets):
        self.secrets = secrets
        w3_provider = Web3.HTTPProvider(endpoint_uri='https://bsc-dataseed.binance.org:443')
        self.w3 = Web3(provider=w3_provider)
        self.addr = NetworkAddresses()
        self.contracts = NetworkContracts(addr=self.addr, w3=self.w3, api_key=secrets.bscscan_api_key)

    def get_bnb_balance(self, wallet: ChecksumAddress) -> Wei:
        return Wei(self.w3.eth.get_balance(wallet))

    @cached(cache=LRUCache(maxsize=256))
    def get_token_decimals(self, token_address: ChecksumAddress) -> int:
        token_contract = self.w3.eth.contract(
            address=token_address, abi=fetch_abi(contract=token_address, api_key=self.secrets.bscscan_api_key)
        )
        decimals = token_contract.functions.decimals().call()
        return int(decimals)

    @cached(cache=LRUCache(maxsize=256))
    def get_token_symbol(self, token_address: ChecksumAddress) -> str:
        token_contract = self.w3.eth.contract(
            address=token_address, abi=fetch_abi(contract=token_address, api_key=self.secrets.bscscan_api_key)
        )
        symbol = token_contract.functions.symbol().call()
        return symbol

    def get_gas_price(self) -> Wei:
        return self.w3.eth.gas_price
