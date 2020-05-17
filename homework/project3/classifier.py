import argparse
import os
from typing import Any

import torch
import torchvision
import torch.optim as optim
from torch.utils.data import DataLoader
from torch import nn
from show_status import show_loss
from data import get_data, input_from_file
from const import *

torch.set_default_tensor_type(torch.FloatTensor)


class Net(nn.Module):
    def __init__(self):
        super(Net, self).__init__()
        # conv1_1 block: nx512x3x3 -> nx128x3x3
        self.conv1_1 = nn.Conv2d(512, 512, 3, 1, padding=0)
        # active
        self.relu1_1 = nn.PReLU()

        self.full_connect = nn.Linear(512, 62)

        # softmax
        self.soft_max = nn.Softmax(1)

    def forward(self, x):
        x = self.relu1_1(self.conv1_1(x))
        x = self.full_connect(x.view(-1, 512))

        x = self.soft_max(x)
        return x.view(-1, CATEGORY_NUM)


class Argument:
    def __init__(self, arguments):
        self.batch_size = arguments.batch_size
        self.test_batch_size = arguments.test_batch_size
        self.epoch = arguments.epoch
        self.lr = arguments.lr
        self.momentum = arguments.momentum
        self.seed = arguments.seed
        self.log_interval = arguments.log_interval
        self.save_model = arguments.save_model
        self.save_directory = arguments.save_directory
        self.phase = arguments.phase
        self.model = arguments.model
        self.input = arguments.input
        self.gpu_no = arguments.gpu_no
        self.model_save_prefix = arguments.model_save_prefix


def train(args: Argument, train_data_loader: DataLoader, valid_data_loader: DataLoader,
          model: Net, criterion: nn.CrossEntropyLoss, optimizer: optim.Optimizer, device):
    # backbone nx3x112x112
    vgg16 = torchvision.models.vgg16(pretrained=True)
    vgg16_features = vgg16.features.to(device)

    if args.save_model:
        if not os.path.exists(args.save_directory):
            os.makedirs(args.save_directory)

    train_losses = []
    valid_losses = []
    for epoch_id in range(args.epoch):
        ############
        # train the model
        ############
        model.train()
        train_mean_loss = 0
        train_accuracy = 0
        batch_count = 0
        for batch_idx, batch in enumerate(train_data_loader):
            img: torch.Tensor = batch["image"]
            category: torch.Tensor = batch["category"]
            input_img = img.to(device)
            ground_truth = category.to(device)

            # clear the gradients of all optimized variables
            optimizer.zero_grad()
            x: torch.Tensor = vgg16_features(input_img)

            output_pts = model(x)
            y_index = output_pts.argmax(1)
            res = ground_truth == y_index
            rate = res.sum().cpu().item() / len(category)
            train_accuracy += rate

            loss = criterion(output_pts, ground_truth)

            # BP
            loss.backward()
            optimizer.step()
            train_mean_loss += loss.item()
            batch_count += 1

        train_accuracy /= batch_count
        train_mean_loss /= batch_count
        train_losses.append(train_mean_loss)

        # valid
        model.eval()
        with torch.no_grad():
            valid_mean_loss = 0
            valid_accuracy = 0
            batch_count = 0
            for batch_idx, batch in enumerate(valid_data_loader):
                img: torch.Tensor = batch["image"]
                category: torch.Tensor = batch["category"]
                input_img = img.to(device)
                ground_truth = category.to(device)

                output_pts: torch.Tensor = model(vgg16_features(input_img))
                y_index = output_pts.argmax(1)
                res = ground_truth == y_index
                rate = res.sum().cpu().item() / len(category)
                valid_accuracy += rate

                loss = criterion(output_pts, ground_truth)

                valid_mean_loss += loss.item()
                batch_count += 1

            valid_mean_loss /= batch_count
            valid_accuracy /= batch_count
            valid_losses.append(valid_mean_loss)
            print(f'epoch: {epoch_id}, train loss: {train_mean_loss},  train_acc: {train_accuracy};'
                  f' valid loss: {valid_mean_loss}, acc: {valid_accuracy}')

        if args.save_model:
            saved_model_name = os.path.join(args.save_directory, f'{args.phase}_{args.model_save_prefix}_epoch_{epoch_id}.pt')
            print(f'saving ...: {saved_model_name}')
            torch.save(model.state_dict(), saved_model_name)
        show_loss(train_losses, valid_losses, f'log/loss/{args.phase}_epoch{epoch_id}.png')
    return train_losses, valid_losses


def parse_arguments():
    parser = argparse.ArgumentParser(description='Classifier')
    parser.add_argument('--batch-size', type=int, default=128)
    parser.add_argument('--test-batch-size', type=int, default=64)
    parser.add_argument('--epoch', type=int, default=5000)
    parser.add_argument('--lr', type=float, default=0.001)
    parser.add_argument('--momentum', type=float, default=0.9)
    """
    store_true' and 'store_false' - These are special cases of 'store_const' using for storing the values True and False 
    respectively. In addition, they create default values of False and True respectively. 
    """
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--log-interval', type=int, default=20,
                        help='how many batches to wait before logging training status')
    parser.add_argument('--save-model', action='store_false') # ??true
    parser.add_argument('--save-directory', type=str, default='trained_models',
                        help='learnt models are saving here')
    parser.add_argument('--model-save-prefix', type=str, help="prefix for model saving name")

    parser.add_argument('--phase', type=str, default='train',   # Train/train, Predict/predict, Finetune/finetune
                        help='training, predicting or finetuning')
    parser.add_argument('--model', type=str,
                        help='the model to load')
    parser.add_argument('--input', type=str, help='path of input image')
    parser.add_argument('--gpu-no', type=int, default=0, help='GPU id, eg: 0, 1, 2 ...')
    return Argument(parser.parse_args())


if __name__ == '__main__':
    # make required dirs
    dirs = ["log/loss"]
    for dir_ in dirs:
        if not os.path.exists(dir_):
            os.makedirs(dir_)

    # parse arguments
    args = parse_arguments()

    torch.manual_seed(args.seed)

    torch.cuda.set_device(args.gpu_no)
    device = torch.device('cuda')
    model = Net().to(device)
    criterion_pts = nn.CrossEntropyLoss()
    params = filter(lambda p: p.requires_grad, model.parameters())

    if args.model and os.path.exists(args.model):
        print(f"loading model: {args.model}")
        saved_status = torch.load(args.model)
        model.load_state_dict(saved_status)
    else:
        print("warning: model not found")

    # todo predict
    if args.phase == 'predict':
        model.eval()
        with torch.no_grad():
            input_x = input_from_file(args.input)
            input_x = input_x.to(device)

            vgg16 = torchvision.models.vgg16(pretrained=True)
            vgg16_features = vgg16.features.to(device)
            x = vgg16_features(input_x)
            # output: torch.Tensor = model.forward(x)
            output: torch.Tensor = model(x)
            print(output)
            print(f'sum: {output.sum()}, max: {output.max()}, category: {output.argmax(1)}')
            exit(0)

    # not predict
    train_data, valid_data = get_data()
    train_data_loader = DataLoader(train_data, args.batch_size, shuffle=True)
    valid_data_loader = DataLoader(valid_data, args.batch_size)

    if args.phase == 'train':
        # optimizer = optim.Adam(params, args.lr, weight_decay=0.001)
        optimizer = optim.Adadelta(params, args.lr, weight_decay=0.001)
        train(args, train_data_loader, valid_data_loader, model, criterion_pts, optimizer, device)
    elif args.phase == 'finetune':
        optimizer = optim.SGD(params, args.lr, args.momentum, weight_decay=0.001)
        train(args, train_data_loader, valid_data_loader, model, criterion_pts, optimizer, device)
