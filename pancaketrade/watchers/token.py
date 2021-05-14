"""Token watcher."""
from typing import List
from pancaketrade.persistence import Token
from pancaketrade.network import Network
from web3 import Web3
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger


class TokenWatcher:
    def __init__(self, token_record: Token, net: Network, interval: float = 5):
        self.net = net
        self.token_record = token_record
        self.address = Web3.toChecksumAddress(token_record.address)
        self.decimals = int(token_record.decimals)
        self.symbol = str(token_record.symbol)
        emoji = token_record.icon + ' ' if token_record.icon else ''
        self.name = emoji + self.symbol
        self.default_slippage = token_record.default_slippage
        self.orders: List = []
        self.interval = interval
        self.scheduler = BackgroundScheduler(job_defaults={'coalesce': False, 'max_instances': 1})

    def start_monitoring(self):
        trigger = IntervalTrigger(seconds=self.interval)
        self.scheduler.add_job(self.monitor_price, trigger=trigger)
        self.scheduler.start()

    def monitor_price(self):
        selling_price = self.net.get_token_price(token_address=self.address, token_decimals=self.decimals, sell=True)
        buying_price = self.net.get_token_price(token_address=self.address, token_decimals=self.decimals, sell=False)
