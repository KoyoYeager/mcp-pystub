"""テスト用: シンプルなプロジェクトのエントリーポイント"""

from helper import process_data


def main():
    result = process_data("input.dat")
    print(result)


if __name__ == "__main__":
    main()
