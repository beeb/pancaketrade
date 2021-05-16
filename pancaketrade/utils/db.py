"""Database helpers."""
from typing import Dict

from pancaketrade.persistence import Abi, Order, Token, db
from pancaketrade.watchers import TokenWatcher
from peewee import fn
from web3.types import ChecksumAddress
from telegram.ext import Dispatcher
from pancaketrade.utils.config import Config


def init_db() -> None:
    with db:
        db.create_tables([Token, Abi, Order])


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
    token_record.delete_instance(recursive=True)


def remove_order(order_record: Order):
    order_record.delete_instance()
