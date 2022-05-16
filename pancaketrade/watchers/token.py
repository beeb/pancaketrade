"""Token watcher."""
from decimal import Decimal
from typing import List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger
from telegram.ext import Dispatcher
from web3 import Web3

from pancaketrade.network import Network
from pancaketrade.persistence import Token
from pancaketrade.utils.config import Config
from pancaketrade.watchers.order import OrderWatcher


class TokenWatcher:
    def __init__(
        self, token_record: Token, net: Network, dispatcher: Dispatcher, config: Config, orders: Optional[List] = None
    ):
        if orders is None:
            orders = []
        self.net = net
        self.dispatcher = dispatcher
        self.config = config
        self.token_record = token_record
        self.address = Web3.toChecksumAddress(token_record.address)
        self.decimals = int(token_record.decimals)
        self.symbol = str(token_record.symbol)
        self.emoji = token_record.icon + " " if token_record.icon else ""
        self.name = self.emoji + self.symbol
        self.default_slippage = Decimal(token_record.default_slippage)
        self.effective_buy_price: Optional[Decimal] = (
            Decimal(token_record.effective_buy_price) if token_record.effective_buy_price else None
        )
        self.orders: List[OrderWatcher] = [
            OrderWatcher(
                order_record=order_record,
                net=self.net,
                dispatcher=self.dispatcher,
                chat_id=self.config.secrets.admin_chat_id,
                price_in_usd=self.config.price_in_usd,
                max_price_impact=self.config.max_price_impact,
            )
            for order_record in orders
        ]
        self.interval = self.config.monitor_interval
        self.scheduler = BackgroundScheduler(
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": max(1, int(0.8 * self.interval))}
        )
        self.last_status_message_id: Optional[int] = None
        self.start_monitoring()

    def start_monitoring(self):
        trigger = IntervalTrigger(seconds=self.interval)
        self.scheduler.add_job(self.monitor_price, trigger=trigger)
        self.scheduler.start()

    def stop_monitoring(self):
        self.scheduler.shutdown(wait=False)

    def monitor_price(self):
        self.update_effective_buy_price()
        if not self.orders:
            return
        price, _ = self.net.get_token_price(token_address=self.address)  # either USD or BNB depending on config
        indices_to_remove: List[int] = []
        for i, order in enumerate(self.orders):
            if order.finished:
                indices_to_remove.append(i)
                continue
            if not self.net.is_approved(token_address=self.address) and order.type == "sell":
                # when selling we require that the token is approved on pcs beforehand
                logger.info(f"Need to approve {self.symbol} for trading on PancakeSwap.")
                self.dispatcher.bot.send_message(
                    chat_id=self.config.secrets.admin_chat_id,
                    text=f"Approving {self.symbol} for trading on PancakeSwap...",
                )
                res = self.net.approve(token_address=self.address)
                if res:
                    self.dispatcher.bot.send_message(
                        chat_id=self.config.secrets.admin_chat_id, text="✅ Approval successful!"
                    )
                else:
                    self.dispatcher.bot.send_message(
                        chat_id=self.config.secrets.admin_chat_id, text="⛔ Approval failed"
                    )
            order.price_update(price=price)
        self.orders = [o for i, o in enumerate(self.orders) if i not in indices_to_remove]

    def update_effective_buy_price(self):
        self.effective_buy_price = (
            Decimal(self.token_record.effective_buy_price) if self.token_record.effective_buy_price else None
        )

    def approve(self) -> bool:
        return self.net.approve(self.address)
