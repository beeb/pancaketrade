"""Token watcher."""
from typing import List
from pancaketrade.persistence import Token
from pancaketrade.network import Network
from web3 import Web3


class TokenWatcher:
    def __init__(self, token_record: Token, net: Network):
        self.net = net
        self.token_record = token_record
        self.address = Web3.toChecksumAddress(token_record.address)
        self.decimals = int(token_record.decimals)
        self.symbol = str(token_record.symbol)
        emoji = token_record.icon + ' ' if token_record.icon else ''
        self.name = emoji + self.symbol
        self.default_slippage = token_record.default_slippage
        self.orders: List = []

    def start_monitoring(self):
        pass
