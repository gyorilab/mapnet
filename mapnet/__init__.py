import logging

__version__ = '0.1.0'

logging.basicConfig(format=('%(levelname)s: [%(asctime)s] %(name)s'
                            ' - %(message)s'),
                    level=logging.INFO, datefmt='%Y-%m-%d %H:%M:%S')

# Suppress INFO-level logging from some dependencies
logging.getLogger('pyobo').setLevel(logging.ERROR)

logger = logging.getLogger('mapnet')
