from decimal import Decimal

from app.application.authoring import ModelRoute, Price, PricingCatalog, Usage


def test_pricing_uses_mutually_exclusive_usage_buckets_once():
    price=Price("USD","audit-v1","unit-test",Decimal("0.010"),Decimal("0.040"),Decimal("0.020"),Decimal("0.030"))
    usage=Usage(input_tokens=100,cache_read_tokens=20,cache_write_tokens=10,output_tokens=30)
    cost=PricingCatalog({("provider","model"):price}).calculate(ModelRoute("provider","model"),usage)

    expected=(Decimal(100)*Decimal("0.010") + Decimal(20)*Decimal("0.020") +
              Decimal(10)*Decimal("0.030") + Decimal(30)*Decimal("0.040"))
    assert expected==Decimal("2.900")
    assert cost.amount==Decimal("2.90000000")
    assert usage.cached_tokens==20


def test_zero_usage_has_zero_decimal_cost():
    price=Price("USD","audit-v1","unit-test",Decimal("1"),Decimal("2"),Decimal("3"),Decimal("4"))
    cost=PricingCatalog({("provider","model"):price}).calculate(ModelRoute("provider","model"),Usage(0,0,0,0))
    assert cost.amount==Decimal("0E-8")
