"""Token watcher."""
from typing import List

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from pancaketrade.network import Network
from pancaketrade.persistence import Token
from pancaketrade.watchers.order import OrderWatcher
from web3 import Web3


class TokenWatcher:
    def __init__(self, token_record: Token, net: Network, interval: float = 5, orders: List = []):
        self.net = net
        self.token_record = token_record
        self.address = Web3.toChecksumAddress(token_record.address)
        self.decimals = int(token_record.decimals)
        self.symbol = str(token_record.symbol)
        emoji = token_record.icon + ' ' if token_record.icon else ''
        self.name = emoji + self.symbol
        self.default_slippage = token_record.default_slippage
        self.orders: List[OrderWatcher] = [
            OrderWatcher(order_record=order_record, net=self.net) for order_record in orders
        ]
        self.interval = interval
        self.scheduler = BackgroundScheduler(
            job_defaults={'coalesce': True, 'max_instances': 1, 'misfire_grace_time': 0.8 * interval}
        )
        self.start_monitoring()

    def start_monitoring(self):
        trigger = IntervalTrigger(seconds=self.interval)
        self.scheduler.add_job(self.monitor_price, trigger=trigger)
        self.scheduler.start()

    def monitor_price(self):
        if not self.orders:
            return
        sell_price = self.net.get_token_price(token_address=self.address, token_decimals=self.decimals, sell=True)
        if self.net.has_both_versions(token_address=self.address):
            buy_price = self.net.get_token_price(token_address=self.address, token_decimals=self.decimals, sell=False)
        else:
            buy_price = sell_price
        for order in self.orders:
            order.price_update(sell_price=sell_price, buy_price=buy_price)
