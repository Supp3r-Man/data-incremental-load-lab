import argparse

from .bench import render, simulate


def main():
    p = argparse.ArgumentParser(prog="incload")
    p.add_argument("command", choices=["demo"])
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--days", type=int, default=20)
    a = p.parse_args()
    print(render(simulate(seed=a.seed, days=a.days)))


if __name__ == "__main__":
    main()
