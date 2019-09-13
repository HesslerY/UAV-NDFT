# --------------------------------------------------------
# Pytorch FPN implementation
# Licensed under The MIT License [see LICENSE for details]
# Written by Jianwei Yang, based on code from faster R-CNN
# --------------------------------------------------------

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import _init_paths
import os
import sys
import numpy as np
import argparse
import pprint
import pdb
import time
import cv2
import cPickle
import torch
from torch.autograd import Variable
import torch.nn as nn
import torch.optim as optim

from roi_data_layer.roidb import combined_roidb
from roi_data_layer.roibatchLoader import roibatchLoader
from model.utils.config import cfg, cfg_from_file, cfg_from_list, get_output_dir
from model.rpn.bbox_transform import clip_boxes
from model.nms.nms_wrapper import nms
from model.rpn.bbox_transform import bbox_transform_inv
from model.utils.net_utils import vis_detections

from model.fpn.resnet import resnet

import pdb

try:
    xrange
except NameError:
    xrange = range

def parse_args():
    """
    Parse input arguments
    """
    parser = argparse.ArgumentParser(description='Train a Fast R-CNN network')
    parser.add_argument('--dataset', dest='dataset',
                        help='training dataset',
                        default='uav', type=str)
    parser.add_argument('--cfg', dest='cfg_file',
                        help='optional config file',
                        default='cfgs/vgg16.yml', type=str)
    parser.add_argument('--net', dest='net',
                        help='vgg16, res50, res101, res152',
                        default='res101', type=str)
    parser.add_argument('--set', dest='set_cfgs',
                        help='set config keys', default=None,
                        nargs=argparse.REMAINDER)
    parser.add_argument('--save_dir', dest='save_dir',
                        help='directory to save models', default="models",
                        type=str)
    parser.add_argument('--cuda', dest='cuda',
                        help='whether use CUDA',
                        action='store_true')
    parser.add_argument('--ls', dest='large_scale',
                        help='whether use large imag scale',
                        action='store_true')
    parser.add_argument('--mGPUs', dest='mGPUs',
                        help='whether use multiple GPUs',
                        action='store_true')
    parser.add_argument('--cag', dest='class_agnostic',
                        help='whether perform class_agnostic bbox regression',
                        action='store_true')
    parser.add_argument('--parallel_type', dest='parallel_type',
                        help='which part of model to parallel, 0: all, 1: model before roi pooling',
                        default=0, type=int)
    parser.add_argument('--checksession', dest='checksession',
                        help='checksession to load model',
                        default=1, type=int)
    parser.add_argument('--checkepoch', dest='checkepoch',
                        help='checkepoch to load network',
                        default=4, type=int)
    parser.add_argument('--checkpoint', dest='checkpoint',
                        help='checkpoint to load network',
                        default=3960, type=int)
    parser.add_argument('--vis', dest='vis',
                        help='visualization mode',
                        action='store_true')
    parser.add_argument('--model_dir', dest='model_dir',
                        help='directory to save models', default="models",
                        type=str)
    parser.add_argument('--gamma_altitude', dest='gamma_altitude',
                        help='the gamma is used to control the relative weight of the adversarial loss of altitude',
                        type=float, required=True)
    parser.add_argument('--gamma_angle', dest='gamma_angle',
                        help='the gamma is used to control the relative weight of the adversarial loss of viewing angle',
                        type=float, required=True)
    parser.add_argument('--gamma_weather', dest='gamma_weather',
                        help='the gamma is used to control the relative weight of the adversarial loss of weather',
                        type=float, required=True)
    parser.add_argument('--use_restarting', dest='use_restarting',
                        help='where to use restarting',
                        action='store_true')
    parser.add_argument('--is_baseline_method', dest='is_baseline_method',
                        help='whether to evaluate the baseline method',
                        action='store_true')
    parser.add_argument('--ovthresh', dest='ovthresh',
                        help='the IoU threshold for evaluation',
                        default=0.7, type=float)
    args = parser.parse_args()
    return args

lr = cfg.TRAIN.LEARNING_RATE
momentum = cfg.TRAIN.MOMENTUM
weight_decay = cfg.TRAIN.WEIGHT_DECAY

if __name__ == '__main__':

    args = parse_args()

    print('Called with args:')
    print(args)

    if torch.cuda.is_available() and not args.cuda:
        print("WARNING: You have a CUDA device, so you should probably run with --cuda")

    args.imdb_name = "uav_2017_trainval"
    args.imdbval_name = "uav_2017_test"
    args.set_cfgs = ['FPN_ANCHOR_SCALES', '[16, 32, 64, 128, 256]', 'FPN_FEAT_STRIDES', '[4, 8, 16, 32, 64]', 'MAX_NUM_GT_BOXES', '60']

    args.cfg_file = "cfgs/{}_ls.yml".format(args.net) if args.large_scale else "cfgs/{}.yml".format(args.net)

    if args.cfg_file is not None:
        cfg_from_file(args.cfg_file)
    if args.set_cfgs is not None:
        cfg_from_list(args.set_cfgs)

    print('Using config:')
    pprint.pprint(cfg)

    cfg.TRAIN.USE_FLIPPED = False
    imdb, roidb, ratio_list, ratio_index = combined_roidb(args.imdbval_name, False)
    imdb.competition_mode(on=True)
    imdb.set_gamma_altitude(args.gamma_altitude)
    imdb.set_gamma_angle(args.gamma_angle)
    imdb.set_gamma_weather(args.gamma_weather)
    imdb.set_epoch(args.checkepoch)
    imdb.set_ckpt(args.checkpoint)
    print('{:d} roidb entries'.format(len(roidb)))


    if args.gamma_altitude > 1e-10 and args.gamma_angle > 1e-10 and args.gamma_weather > 1e-10:
        nuisance_type = "A+V+W"
    elif args.gamma_altitude > 1e-10 and args.gamma_angle > 1e-10:
        nuisance_type = "A+V"
    elif args.gamma_altitude > 1e-10 and args.gamma_weather > 1e-10:
        nuisance_type = "A+W"
    elif args.gamma_altitude > 1e-10:
        nuisance_type = "A"
    elif args.gamma_angle > 1e-10:
        nuisance_type = "V"
    elif args.gamma_weather > 1e-10:
        nuisance_type = "W"
    else:
        nuisance_type ="Baseline"

    model_dir = os.path.join(args.model_dir, nuisance_type, 'altitude={}_angle={}_weather={}'.format(str(args.gamma_altitude), str(args.gamma_angle), str(args.gamma_weather)))
    if not os.path.exists(model_dir):
        raise Exception('There is no input directory for loading network from ' + model_dir)

    load_name = os.path.join(model_dir,
                             'fpn_{}_{}_{}_adv.pth'.format(args.checksession, args.checkepoch, args.checkpoint))

    # initilize the network here.

    if args.net == 'res101':
        fpn = resnet(imdb.classes, 101, pretrained=True, class_agnostic=args.class_agnostic)
    elif args.net == 'res50':
        fpn = resnet(imdb.classes, 50, pretrained=True, class_agnostic=args.class_agnostic)
    elif args.net == 'res152':
        fpn = resnet(imdb.classes, 152, pretrained=True, class_agnostic=args.class_agnostic)
    else:
        print("network is not defined")
        pdb.set_trace()

    fpn.create_architecture()

    print("load checkpoint %s" % (load_name))
    checkpoint = torch.load(load_name)

    for key in checkpoint['model'].keys():
        if any(name in key for name in ['RCNN_angle_score', 'RCNN_altitude_score', 'RCNN_weather_score', 'RCNN_angle', 'RCNN_weather', 'RCNN_altitude']):
            del checkpoint['model'][key]

    model_dict = fpn.state_dict()
    model_dict.update(checkpoint['model'])
    fpn.load_state_dict(model_dict)

    if 'pooling_mode' in checkpoint.keys():
        cfg.POOLING_MODE = checkpoint['pooling_mode']

    print('load model successfully!')
    im_data = torch.FloatTensor(1)
    im_info = torch.FloatTensor(1)
    meta_data = torch.FloatTensor(1)
    num_boxes = torch.LongTensor(1)
    gt_boxes = torch.FloatTensor(1)

    # ship to cuda
    if args.cuda:
        im_data = im_data.cuda()
        im_info = im_info.cuda()
        meta_data = meta_data.cuda()
        num_boxes = num_boxes.cuda()
        gt_boxes = gt_boxes.cuda()

    # make variable
    im_data = Variable(im_data)
    im_info = Variable(im_info)
    meta_data = Variable(meta_data)
    num_boxes = Variable(num_boxes)
    gt_boxes = Variable(gt_boxes)

    if args.cuda:
        cfg.CUDA = True

    if args.cuda:
        fpn.cuda()

    start = time.time()
    max_per_image = 100

    vis = args.vis

    if vis:
        thresh = 0.0
    else:
        thresh = 0.0

    save_name = 'fpn'
    num_images = len(imdb.image_index)
    all_boxes = [[[] for _ in xrange(num_images)]
                 for _ in xrange(imdb.num_classes)]

    output_dir = get_output_dir(imdb, save_name)
    dataset = roibatchLoader(roidb, ratio_list, ratio_index, 1, \
                             imdb.num_classes, training=False, normalize=False)
    dataloader = torch.utils.data.DataLoader(dataset, batch_size=1,
                                             shuffle=False, num_workers=4,
                                             pin_memory=True)

    data_iter = iter(dataloader)

    _t = {'im_detect': time.time(), 'misc': time.time()}
    det_file = os.path.join(output_dir, 'detections.pkl')

    fpn.eval()
    empty_array = np.transpose(np.array([[], [], [], [], []]), (1, 0))
    for i in range(num_images):
        data = data_iter.next()
        im_data.data.resize_(data[0].size()).copy_(data[0])
        im_info.data.resize_(data[1].size()).copy_(data[1])
        meta_data.data.resize_(data[2].size()).copy_(data[2])
        gt_boxes.data.resize_(data[3].size()).copy_(data[3])
        num_boxes.data.resize_(data[4].size()).copy_(data[4])

        det_tic = time.time()
        rois, cls_prob, bbox_pred, \
        _, _, _  = fpn(im_data, im_info, meta_data, gt_boxes, num_boxes)

        scores = cls_prob.data
        boxes = rois.data[:, :, 1:5]

        if cfg.TEST.BBOX_REG:
            # Apply bounding-box regression deltas
            box_deltas = bbox_pred.data
            if cfg.TRAIN.BBOX_NORMALIZE_TARGETS_PRECOMPUTED:
                # Optionally normalize targets by a precomputed mean and stdev
                if args.class_agnostic:
                    box_deltas = box_deltas.view(-1, 4) * torch.FloatTensor(cfg.TRAIN.BBOX_NORMALIZE_STDS).cuda() \
                                 + torch.FloatTensor(cfg.TRAIN.BBOX_NORMALIZE_MEANS).cuda()
                    box_deltas = box_deltas.view(1, -1, 4)
                else:
                    box_deltas = box_deltas.view(-1, 4) * torch.FloatTensor(cfg.TRAIN.BBOX_NORMALIZE_STDS).cuda() \
                                 + torch.FloatTensor(cfg.TRAIN.BBOX_NORMALIZE_MEANS).cuda()
                    box_deltas = box_deltas.view(1, -1, 4 * len(imdb.classes))

            pred_boxes = bbox_transform_inv(boxes, box_deltas, 1)
            pred_boxes = clip_boxes(pred_boxes, im_info.data, 1)
        else:
            # Simply repeat the boxes, once for each class
            pred_boxes = boxes

        pred_boxes /= data[1][0][2].item()

        scores = scores.squeeze()
        pred_boxes = pred_boxes.squeeze()
        det_toc = time.time()
        detect_time = det_toc - det_tic
        misc_tic = time.time()
        if vis:
            im = cv2.imread(imdb.image_path_at(i))
            im2show = np.copy(im)
        for j in xrange(1, imdb.num_classes):
            inds = torch.nonzero(scores[:, j] > thresh).view(-1)
            # if there is det
            if inds.numel() > 0:
                cls_scores = scores[:, j][inds]
                _, order = torch.sort(cls_scores, 0, True)
                if args.class_agnostic:
                    cls_boxes = pred_boxes[inds, :]
                else:
                    cls_boxes = pred_boxes[inds][:, j * 4:(j + 1) * 4]

                cls_dets = torch.cat((cls_boxes, cls_scores.unsqueeze(1)), 1)
                cls_dets = cls_dets[order]
                #print(cls_dets)
                keep = nms(cls_dets, cfg.TEST.NMS, force_cpu=False)
                cls_dets = cls_dets[keep.view(-1).long()]
                if vis:
                    im2show = vis_detections(im2show, imdb.classes[j], cls_dets.cpu().numpy(), 0.3)
                all_boxes[j][i] = cls_dets.cpu().numpy()
            else:
                all_boxes[j][i] = empty_array

        # Limit to max_per_image detections *over all classes*
        if max_per_image > 0:
            image_scores = np.hstack([all_boxes[j][i][:, -1]
                                      for j in xrange(1, imdb.num_classes)])
            all_boxes[j][i] = np.concatenate(
                (all_boxes[j][i], np.tile(meta_data.cpu().numpy(), (len(image_scores), 1))),
                axis=1)
            if len(image_scores) > max_per_image:
                image_thresh = np.sort(image_scores)[-max_per_image]
                for j in xrange(1, imdb.num_classes):
                    keep = np.where(all_boxes[j][i][:, -1] >= image_thresh)[0]
                    all_boxes[j][i] = all_boxes[j][i][keep, :]

        misc_toc = time.time()
        nms_time = misc_toc - misc_tic

        sys.stdout.write('im_detect: {:d}/{:d} {:.3f}s {:.3f}s   \r' \
                         .format(i + 1, num_images, detect_time, nms_time))
        sys.stdout.flush()

        if vis:
            cv2.imwrite('images/result%d.png' % (i), im2show)
            pdb.set_trace()
            # cv2.imshow('test', im2show)
            # cv2.waitKey(0)

    with open(det_file, 'wb') as f:
        cPickle.dump(all_boxes, f, cPickle.HIGHEST_PROTOCOL)

    print('Evaluating detections')

    imdb.evaluate_detections(all_boxes, output_dir, nuisance_type=nuisance_type, baseline_method=args.is_baseline_method, ovthresh=args.ovthresh)

    end = time.time()
    print("test time: %0.4fs" % (end - start))
