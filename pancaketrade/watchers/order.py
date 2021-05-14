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
        self.amount = Wei(int(order_record.amount))  # in wei, either BNB (buy) or token (sell) depending on "type"
        self.slippage = order_record.slippage
        self.gas_price: Optional[Wei] = (
            Wei(int(order_record.gas_price)) if order_record.gas_price else None
        )  # in wei, if null then use network gas price
        self.created = order_record.created
        self.min_price: Optional[Decimal] = None
        self.max_price: Optional[Decimal] = None

    def price_update(self, sell_price: Decimal, buy_price: Decimal):
        if self.type == 'buy':
            logger.info(buy_price)
        else:
            logger.info(sell_price)

        if self.type == 'buy' and self.trailing_stop is None and self.above is False and buy_price <= self.limit_price:
            logger.success('Limit buy triggered')  # buy
        elif (
            self.type == 'sell'
            and self.trailing_stop is None
            and self.above is False
            and sell_price <= self.limit_price
        ):
            logger.warning('Stop loss triggered')  # sell
        elif (
            self.type == 'sell' and self.trailing_stop is None and self.above is True and sell_price >= self.limit_price
        ):
            logger.success('Take profit triggered')  # sell
        elif (
            self.type == 'buy'
            and self.trailing_stop
            and self.above is False
            and (buy_price <= self.limit_price or self.min_price is not None)
        ):
            if self.min_price is None:
                self.min_price = buy_price
            rise = ((buy_price / self.min_price) - Decimal(1)) * Decimal(100)
            if buy_price < self.min_price:
                self.min_price = buy_price
                return
            elif rise > self.trailing_stop:
                logger.success('Trailing stop loss triggered')  # buy
                return
        elif (
            self.type == 'sell'
            and self.trailing_stop
            and self.above is True
            and (sell_price >= self.limit_price or self.max_price is not None)
        ):
            if self.max_price is None:
                self.max_price = sell_price
            drop = (Decimal(1) - (sell_price / self.max_price)) * Decimal(100)
            if sell_price > self.max_price:
                self.max_price = sell_price
                return
            elif drop > self.trailing_stop:
                logger.success('Trailing stop loss triggered')  # sell
                return
