"""テスト用: heavylib を import するが一部機能のみ使うヘルパー"""

from heavylib.core import CoreProcessor


def process_data(path):
    """CoreProcessor を使う — heavylib.core のみ必要"""
    proc = CoreProcessor()
    return proc.run(path)


def export_report(data):
    """ReportGenerator を使う — heavylib.optional_feature + pandas が必要
    しかし main.py からは呼ばれない → pandas は stubbable"""
    from heavylib.optional_feature import ReportGenerator
    gen = ReportGenerator()
    return gen.generate(data)
