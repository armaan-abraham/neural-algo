import configparser

if __name__ == "__main__":
    config = configparser.ConfigParser()
    config['Defaults'] = {
        'normaliseloss': False,
        'nnodes': ['1'],
        'ngraphs': ['1'],
        'datagraphsetname': ['joe'],
        'epochs': 50,
    }
    with open('options.conf', 'w') as configfile:
        config.write(configfile)



normaliseloss=False


""""python main.py --datagen True --nnodes "6" --ngraphs "1" --graphtypes "TwoDGrid"""""