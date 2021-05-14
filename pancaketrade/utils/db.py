"""Database helpers."""
from typing import Dict

from pancaketrade.persistence import Abi, Order, Token, db
from pancaketrade.watchers import TokenWatcher
from peewee import fn
from web3.types import ChecksumAddress


def init_db() -> None:
    with db:
        db.create_tables([Token, Abi, Order])


def token_exists(address: ChecksumAddress) -> bool:
    with db:
        count = Token.select().where(Token.address == str(address)).count()
    return count > 0


def get_token_watchers(net) -> Dict[str, TokenWatcher]:
    out: Dict[str, TokenWatcher] = {}
    with db:
        for token_record in Token.select().order_by(fn.Lower(Token.symbol)):
            out[token_record.address] = TokenWatcher(token_record=token_record, net=net)
    return out
