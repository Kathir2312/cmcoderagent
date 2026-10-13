import argparse


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--name", default="world")
    args = p.parse_args(argv)
    greeting = f"Hello, {args.name}!"
    print(greeting)


if __name__ == "__main__":
    main()
