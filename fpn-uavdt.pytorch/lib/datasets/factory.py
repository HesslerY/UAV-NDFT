# --------------------------------------------------------
# Fast R-CNN
# Copyright (c) 2015 Microsoft
# Licensed under The MIT License [see LICENSE for details]
# Written by Ross Girshick
# --------------------------------------------------------

"""Factory method for easily getting imdbs by name."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

__sets = {}
from datasets.visdrone import visdrone
from datasets.coco import coco
from datasets.uav import uav

try:
    FileNotFoundError
except NameError:
    FileNotFoundError = IOError

import numpy as np

# Set up coco_2014_<split>
for year in ['2014']:
  for split in ['train', 'val', 'minival', 'valminusminival', 'trainval']:
    name = 'coco_{}_{}'.format(year, split)
    __sets[name] = (lambda split=split, year=year: coco(split, year))

# Set up coco_2014_cap_<split>
for year in ['2014']:
  for split in ['train', 'val', 'capval', 'valminuscapval', 'trainval']:
    name = 'coco_{}_{}'.format(year, split)
    __sets[name] = (lambda split=split, year=year: coco(split, year))

# Set up coco_2015_<split>
for year in ['2015']:
  for split in ['test', 'test-dev']:
    name = 'coco_{}_{}'.format(year, split)
    __sets[name] = (lambda split=split, year=year: coco(split, year))

for year in ['2017']:
  for split in ['trainval', 'test', 'trainvaltest']:
    name = 'uav_{}_{}'.format(year, split)
    __sets[name] = (lambda split=split, year=year: uav(split, year))

# Set up voc_<year>_<split>
for year in ['2017']:
  for split in ['trainval', 'test']:
    name = 'visdrone_{}_{}'.format(year, split)
    __sets[name] = (lambda split=split, year=year: visdrone(split, year))

def get_imdb(name):
  """Get an imdb (image database) by name."""
  if name not in __sets:
    raise KeyError('Unknown dataset: {}'.format(name))
  return __sets[name]()


def list_imdbs():
  """List all registered imdbs."""
  return list(__sets.keys())
