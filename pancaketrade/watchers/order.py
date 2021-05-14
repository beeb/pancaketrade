"""Order watcher."""
from decimal import Decimal
from typing import Optional

from loguru import logger
from pancaketrade.network import Network
from pancaketrade.persistence import Order
from web3.types import Wei


class OrderWatcher:
    def __init__(self, order_record: Order, net: Network):
        self.order_record = order_record
        self.token_record = order_record.token
        self.net = net

        self.type = order_record.type  # buy (tokens for BNB) or sell (tokens for BNB)
        self.limit_price = Decimal(order_record.limit_price)  # decimal stored as string
        self.above = order_record.above  # Above = True, below = False
        self.trailing_stop: Optional[int] = order_record.trailing_stop  # in percent
        self.amount = Wei(int(order_record.amount))  # in wei, either BNB or token depending on "type"
        self.slippage = order_record.slippage
        self.gas_price: Optional[Wei] = (
            Wei(int(order_record.gas_price)) if order_record.gas_price else None
        )  # in wei, if null then use network gas price
        self.created = order_record.created

    def price_update(self, sell_price: Decimal, buy_price: Decimal):
        logger.info(sell_price)
        logger.info(buy_price)
