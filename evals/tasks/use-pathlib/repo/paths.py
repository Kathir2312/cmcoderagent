import os


def data_path(name):
    return os.path.dirname(os.path.abspath(__file__)) + '/data/' + name
