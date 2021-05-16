"""Order watcher."""
from decimal import Decimal
from typing import Optional

from loguru import logger
from pancaketrade.network import Network
from pancaketrade.persistence import Order, Token
from web3.types import Wei


class OrderWatcher:
    def __init__(self, order_record: Order, net: Network):
        self.order_record = order_record
        self.token_record: Token = order_record.token
        self.net = net

        self.type = order_record.type  # buy (tokens for BNB) or sell (tokens for BNB)
        self.limit_price = Decimal(order_record.limit_price)  # decimal stored as string
        self.above = order_record.above  # Above = True, below = False
        self.trailing_stop: Optional[int] = order_record.trailing_stop  # in percent
        self.amount = Wei(int(order_record.amount))  # in wei, either BNB (buy) or token (sell) depending on "type"
        self.slippage = order_record.slippage
        # gas price in wei or offset from default in gwei (starts with +), if null then use network gas price
        self.gas_price: Optional[str] = order_record.gas_price
        self.created = order_record.created
        self.active = True
        self.min_price: Optional[Decimal] = None
        self.max_price: Optional[Decimal] = None
        logger.info(self.long_repr())
        logger.info(self)

    def __repr__(self) -> str:
        type_name = self.get_type_name()
        comparision = self.get_comparison_symbol()
        amount = self.get_human_amount()
        unit = self.get_amount_unit()
        trailing = f' tsl {self.trailing_stop}%' if self.trailing_stop is not None else ''
        return (
            f'(#{self.order_record.id}) {self.token_record.symbol} {comparision} {self.limit_price:.3g} BNB - '
            + f'{type_name} {amount:.6g} {unit}{trailing}'
        )

    def long_repr(self) -> str:
        icon = self.token_record.icon + ' ' if self.token_record.icon else ''
        type_name = self.get_type_name()
        comparision = self.get_comparison_symbol()
        amount = self.get_human_amount()
        unit = self.get_amount_unit()
        trailing = f'Trailing stop loss {self.trailing_stop}% callback\n' if self.trailing_stop is not None else ''
        gas_price = (
            f'{Decimal(self.gas_price) / Decimal(10 ** 9):.1g} Gwei'
            if self.gas_price and not self.gas_price.startswith('+')
            else 'network default'
            if self.gas_price is None
            else f'network default {self.gas_price} Gwei'
        )
        return (
            f'{icon}{self.token_record.symbol} - (#{self.order_record.id}) {type_name}\n'
            + trailing
            + f'Amount: {amount:.6g} {unit}\n'
            + f'Price {comparision} {self.limit_price:.3g} BNB\n'
            + f'Slippage: {self.slippage}%\n'
            + f'Gas: {gas_price}'
        )

    def price_update(self, sell_price: Decimal, buy_price: Decimal):
        if not self.active:
            return
        if self.type == 'buy':
            logger.info(buy_price)
        else:
            logger.info(sell_price)

        if self.type == 'buy':
            self.price_update_buy(buy_price=buy_price)
        else:
            self.price_update_sell(sell_price=sell_price)

    def price_update_buy(self, buy_price: Decimal):
        if self.trailing_stop is None and not self.above and buy_price <= self.limit_price:
            logger.success(f'Limit buy triggered at price {buy_price:.3E} BNB')  # buy
            self.close()
            return
        elif self.trailing_stop and not self.above and (buy_price <= self.limit_price or self.min_price is not None):
            if self.min_price is None:
                logger.info(f'Limit condition reached at price {buy_price:.3E} BNB')
                self.min_price = buy_price
            rise = ((buy_price / self.min_price) - Decimal(1)) * Decimal(100)
            if buy_price < self.min_price:
                self.min_price = buy_price
                return
            elif rise > self.trailing_stop:
                logger.success(f'Trailing stop loss triggered at price {buy_price:.3E} BNB')  # buy
                self.close()
                return

    def price_update_sell(self, sell_price: Decimal):
        if self.trailing_stop is None and not self.above and sell_price <= self.limit_price:
            logger.warning(f'Stop loss triggered at price {sell_price:.3E} BNB')
            self.close()
            return
        elif self.trailing_stop is None and self.above and sell_price >= self.limit_price:
            logger.success(f'Take profit triggered at price {sell_price:.3E} BNB')
            self.close()
            return
        elif self.trailing_stop and self.above and (sell_price >= self.limit_price or self.max_price is not None):
            if self.max_price is None:
                logger.info(f'Limit condition reached at price {sell_price:.3E} BNB')
                self.max_price = sell_price
            drop = (Decimal(1) - (sell_price / self.max_price)) * Decimal(100)
            if sell_price > self.max_price:
                self.max_price = sell_price
                return
            elif drop > self.trailing_stop:
                logger.success(f'Trailing stop loss triggered at price {sell_price:.3E} BNB')
                self.close()
                return

    def close(self):
        self.active = False
        if self.type == 'buy':
            logger.info('Buying tokens')
        else:
            logger.info('Selling tokens')

    def get_type_name(self) -> str:
        return (
            'limit buy'
            if self.type == 'buy' and not self.above
            else 'stop loss'
            if self.type == 'sell' and not self.above
            else 'limit sell'
            if self.type == 'sell' and self.above
            else 'unknown'
        )

    def get_comparison_symbol(self) -> str:
        return '&gt;' if self.above else '&lt;'

    def get_human_amount(self) -> Decimal:
        decimals = self.token_record.decimals if self.type == 'sell' else 18
        return Decimal(self.amount) / Decimal(10 ** decimals)

    def get_amount_unit(self) -> str:
        return self.token_record.symbol if self.type == 'sell' else 'BNB'
