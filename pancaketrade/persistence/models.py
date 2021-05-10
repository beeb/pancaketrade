from peewee import CharField, FixedCharField, ForeignKeyField, Model, SmallIntegerField, BooleanField
from playhouse.pool import PooledSqliteDatabase

db = PooledSqliteDatabase('pancaketrade.db', max_connections=20, stale_timeout=20, timeout=0)


class Token(Model):
    address = FixedCharField(max_length=42, unique=True)
    symbol = CharField()
    icon = CharField(null=True)  # emoji
    decimals = SmallIntegerField()

    class Meta:
        database = db


class Order(Model):
    token = ForeignKeyField(Token, backref='orders')
    type = FixedCharField(max_length=4)  # buy or sell
    limit_price = CharField()  # store as string
    above = BooleanField()  # Above = True, below = False
    trailing_stop = SmallIntegerField(null=True)  # in percent
    amount = CharField()

    class Meta:
        database = db


def init_db() -> None:
    global db
    with db:
        db.create_tables([Token, Order])
