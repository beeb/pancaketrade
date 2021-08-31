"""Database helpers."""
from typing import Dict

from loguru import logger
from pancaketrade.persistence import Order, Token, db
from pancaketrade.utils.config import Config
from pancaketrade.watchers.token import TokenWatcher
from peewee import fn, FixedCharField
from playhouse.migrate import SqliteMigrator, migrate
from telegram.ext import Dispatcher
from web3.types import ChecksumAddress


def init_db() -> None:
    with db:
        db.create_tables([Token, Order])
    columns = db.get_columns('token')
    column_names = [c.name for c in columns]
    default_slippage_column = [c for c in columns if c.name == 'default_slippage'][0]
    order_columns = db.get_columns('order')
    order_slippage_column = [c for c in order_columns if c.name == 'slippage'][0]
    migrator = SqliteMigrator(db)
    with db.atomic():
        if 'effective_buy_price' not in column_names:
            migrate(migrator.add_column('token', 'effective_buy_price', Token.effective_buy_price))
        if default_slippage_column.data_type == 'INTEGER':
            migrate(migrator.alter_column_type('token', 'default_slippage', FixedCharField(max_length=7)))
        if order_slippage_column.data_type == 'INTEGER':
            migrate(migrator.alter_column_type('order', 'slippage', FixedCharField(max_length=7)))


def token_exists(address: ChecksumAddress) -> bool:
    with db:
        count = Token.select().where(Token.address == str(address)).count()
    return count > 0


def get_token_watchers(net, dispatcher: Dispatcher, config: Config) -> Dict[str, TokenWatcher]:
    out: Dict[str, TokenWatcher] = {}
    with db:
        for token_record in Token.select().order_by(fn.Lower(Token.symbol)).prefetch(Order):
            out[token_record.address] = TokenWatcher(
                token_record=token_record,
                net=net,
                dispatcher=dispatcher,
                config=config,
                orders=token_record.orders,
            )
    return out


def remove_token(token_record: Token):
    db.connect()
    try:
        token_record.delete_instance(recursive=True)
    except Exception as e:
        logger.error(f'Database error: {e}')
    finally:
        db.close()


def remove_order(order_record: Order):
    db.connect()
    try:
        order_record.delete_instance()
    except Exception as e:
        logger.error(f'Database error: {e}')
    finally:
        db.close()
