import re
from os import path
from setuptools import setup, find_packages


here = path.abspath(path.dirname(__file__))



setup(name='mapnet',
      url='https://github.com/gyorilab/mapnet',
      packages=find_packages(),
      install_requires=[
          ],
      where='mapnet',
      )
