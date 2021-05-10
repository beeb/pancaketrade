from peewee import Model, CharField, FixedCharField
from playhouse.pool import PooledSqliteDatabase

db = PooledSqliteDatabase('pancaketrade.db', max_connections=20, stale_timeout=20, timeout=0)


class Token(Model):
    address = FixedCharField(max_length=42)
    symbol = CharField()
    icon = CharField()

    class Meta:
        database = db


def init_db() -> None:
    global db
    db.connect()
    db.create_tables([Token])
    db.close()
