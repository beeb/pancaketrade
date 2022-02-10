"""Database helpers."""
from decimal import Decimal
from typing import Dict

from loguru import logger
from peewee import FixedCharField, fn
from playhouse.migrate import SqliteMigrator, migrate
from telegram.ext import Dispatcher
from web3.types import ChecksumAddress

from pancaketrade.persistence import Order, Preferences, Token, db
from pancaketrade.utils.config import Config
from pancaketrade.watchers.token import TokenWatcher


def init_db() -> None:
    with db:
        db.create_tables([Token, Order, Preferences])
    columns = db.get_columns("token")
    column_names = [c.name for c in columns]
    default_slippage_column = [c for c in columns if c.name == "default_slippage"][0]
    order_columns = db.get_columns("order")
    order_slippage_column = [c for c in order_columns if c.name == "slippage"][0]
    migrator = SqliteMigrator(db)
    with db.atomic():
        if "effective_buy_price" not in column_names:
            migrate(migrator.add_column("token", "effective_buy_price", Token.effective_buy_price))
        if default_slippage_column.data_type == "INTEGER":
            migrate(migrator.alter_column_type("token", "default_slippage", FixedCharField(max_length=7)))
        if order_slippage_column.data_type == "INTEGER":
            migrate(migrator.alter_column_type("order", "slippage", FixedCharField(max_length=7)))

        count = Preferences.select().where(Preferences.key == "price_in_usd").count()
        if count == 0:
            Preferences.create(key="price_in_usd", value="false")  # for backwards-compatibility


def token_exists(address: ChecksumAddress) -> bool:
    with db:
        count = Token.select().where(Token.address == str(address)).count()
    return count > 0


def get_token_watchers(net, dispatcher: Dispatcher, config: Config) -> Dict[str, TokenWatcher]:
    out: Dict[str, TokenWatcher] = {}
    with db:
        for token_record in Token.select().order_by(fn.Lower(Token.symbol)).prefetch(Order):
            out[token_record.address] = TokenWatcher(
                token_record=token_record, net=net, dispatcher=dispatcher, config=config, orders=token_record.orders
            )
    return out


def remove_token(token_record: Token):
    db.connect(reuse_if_open=True)
    try:
        token_record.delete_instance(recursive=True)
    except Exception as e:
        logger.error(f"Database error: {e}")
    finally:
        db.close()


def remove_order(order_record: Order):
    db.connect(reuse_if_open=True)
    try:
        order_record.delete_instance()
    except Exception as e:
        logger.error(f"Database error: {e}")
    finally:
        db.close()


def update_db_prices(new_price_in_usd: bool, dispatcher: Dispatcher, chat_id: int, net):
    with db:
        old_price_in_usd_pref = Preferences.select().where(Preferences.key == "price_in_usd").get()
    old_price_in_usd = old_price_in_usd_pref.value == "true"

    if old_price_in_usd == new_price_in_usd:  # no action needed
        return
    try:
        with db.atomic():
            for token_record in Token.select():
                if token_record.effective_buy_price is None:
                    continue
                effective_buy_price = Decimal(token_record.effective_buy_price)
                if new_price_in_usd:  # old price in BNB, convert to USD
                    new_effective_price = net.get_bnb_price() * effective_buy_price
                else:  # old price in USD, convert to BNB
                    new_effective_price = effective_buy_price / net.get_bnb_price()
                token_record.effective_buy_price = str(new_effective_price)
                token_record.save()
            for order_record in Order.select():
                if not order_record.limit_price:
                    continue
                limit_price = Decimal(order_record.limit_price)
                if new_price_in_usd:  # old price in BNB, convert to USD
                    new_limit_price = net.get_bnb_price() * limit_price
                else:  # old price in USD, convert to BNB
                    new_limit_price = limit_price / net.get_bnb_price()
                order_record.limit_price = str(new_limit_price)
                order_record.save()
            old_price_in_usd_pref.value = "true" if new_price_in_usd else "false"
            old_price_in_usd_pref.save()
    except Exception as e:
        logger.error(e)
        dispatcher.bot.send_message(chat_id=chat_id, text=f"⛔ Failed to edit database record: {e}")
        return
    logger.info(f"Updated prices in database to match new preference: price_in_usd = {new_price_in_usd}")
