import pickle as pkl

import torch
from torch.utils.data import DataLoader

from dataloaders import Distilled, collate_distilled
import loss
import initialisation as init

# function to reduce bloat in train()
def create_optimizer(logger, optimizer, parameters, lr, weight_decay, options=None):
    """
    options: a dict with options for optimizer, currently unused
    """
    if optimizer == 'sgd':
        return torch.optim.SGD(parameters, lr=lr, weight_decay=weight_decay)
    elif optimizer == 'rmsprop':
        return torch.optim.RMSprop(parameters, lr=lr, weight_decay=weight_decay)
    elif optimizer == 'adagrad':
        return torch.optim.Adagrad(parameters, lr=lr, weight_decay=weight_decay)
    elif optimizer == 'adam':
        return torch.optim.Adam(parameters, lr=lr, weight_decay=weight_decay)
    elif optimizer == 'adamax':
        return torch.optim.Adamax(parameters, lr=lr, weight_decay=weight_decay)
    else:
        raise logger.exception("Unsupported optimizer: {}".format(name))

class EarlyStopping():
    def __init__(self, patience, tolerance=5e-6):
        self.cur_val = None
        self.p = patience
        self.count = 0
        self.tol = tolerance
        self.model_state = None

    def update_meter(self, loss_val, model_state=None):
        if self.cur_val is None:
            self.cur_val = loss_val
            self.model_state = model_state
            return False
        else:
            stop = False
            if self.cur_val-loss_val < self.tol:
                self.count = self.count + 1
                if self.count >= self.p:
                    stop = True
            else:
                self.cur_val = loss_val
                self.model_state = model_state
                self.count = 1
                stop = False
            return stop

def train_metadata():
    return ['optimizer', 'epochs', 'lr',
            'warmup', 'earlystop', 'patience',
            'weightdecay', 'schedpatience',
            'tempinit', 'temprate', 'tempmin',
            'earlytol', 'ksamples', 'task', 'batchsize' ]

def train_metrics_record():
    """
    gradient_norm: regex to capture which weigh tensor gradients should be tracked
    """
    return ['gradient_norm']

def train(logger, device, data_stream, val_stream, model, train_params, loss_module, recorder=None):
    """
    logger: for logging trainig progress
    device: whether to train on gpu or cpu
    data_stream: a pytorch dataloader
    val_stream: a pytorch dataloader
    model: the model to train
    train_params: a dict, containing information like optimizer, lr, epochs, etc
    loss_fn: the training loss function
    val_loss_fn: the validation loss function
    """

    # training parameters that are needed
    algo_name = train_params['task']               # string
    epochs = train_params['epochs']                # positive int
    lr = train_params['lr']                        # positive float
    warm_up_steps = train_params['warmup']         # positive int
    early_stop = train_params['earlystop']         # bool
    early_tol = train_params['earlytol']           # positive small float
    patience = train_params['patience']            # positive int
    sched_patience = train_params['schedpatience'] # positive or 0 int
    temp = train_params['tempinit']                # temp init
    temprate = train_params['temprate']            # temp rate
    tempmin = train_params['tempmin']              # temp min
    k_samples = train_params['ksamples']           # positive int
    bsize = train_params['batchsize']           # positive int

    # priting the training params to the logger
    logger.info("Starting training with the following parameters:")
    logger.info(str(train_params))

    # creating optimizer
    optimizer = create_optimizer(logger,
                                 train_params['optimizer'],
                                 model.parameters(),
                                 train_params['lr'],
                                 train_params['weightdecay']
                                 )

    # scheduler for lr changes
    if sched_patience == 0:
        scheduler = None
    else:
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer,
                                                               'min',
                                                               factor=0.5,
                                                               patience=sched_patience
                                                               )
        # scheduler = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(optimizer,
        #                                                        T_0 = 10,
        #                                                        T_mult = 1,
        #                                                        # eta_min= lr/20
        #                                                        )

    # creating early stopping meter if requested
    if early_stop:
        early_stop_meter = EarlyStopping(patience,
                                         tolerance=early_tol
                                         )

    val_loss = 0
    warmup_steps_done = 0
    nbatches = len(data_stream)
    for epoch in range(epochs):
        model.train()
        cur_loss = 0
        for ith, batch in enumerate(data_stream):
            ## this is specific to the model & data we want to train, consider outsourcing to a function
            # the general scheme is:
            optimizer.zero_grad()

            loss = loss_module.train_loss(logger, device, model, batch)

            # computing the gradient and applying it
            sum(loss).backward()
            cur_loss += sum(loss).item()

            # clip gradients
            torch.nn.utils.clip_grad_norm_(model.parameters(), 8)

            # warm_up
            if warmup_steps_done < warm_up_steps:
                new_lr = (lr /warm_up_steps) * (warmup_steps_done+1)
                warmup_steps_done += 1
                for param_group in optimizer.param_groups:
                    param_group['lr'] = new_lr

            optimizer.step()

        if scheduler is not None and warmup_steps_done >=warm_up_steps:
            scheduler.step(val_loss)
                # scheduler.step(epoch + ith/nbatches)


            # to measure the gradient norm per weight tensor
            # for p in model.parameters():
            #     param_norm = p.grad.data.norm(2).item()


        # eval -- potentially add ability to only do this every mth epoch
        model.eval()
        val_loss = 0
        for ith, batch in enumerate(val_stream):
            with torch.no_grad():
                val_loss += sum(loss_module.val_loss(logger, device, model, batch)).item()

        # log epoch
        logger.info(
            'Epoch {}; Train loss {:.4f}; Val loss {:.4f}'.format(
                epoch,
                cur_loss/nbatches,
                val_loss
            )
        )

        # decide whether to stop or not
        if early_stop:
            stop = early_stop_meter.update_meter(val_loss, model.state_dict())
            if stop:
                logger.info("Early stopping criterion satisfied")
                if early_stop_meter.model_state is not None:
                    model.load_state_dict(early_stop_meter.model_state)
                break

        temp = max(temp*temprate, tempmin)
        model.temp = temp

    return model.state_dict(), val_loss
