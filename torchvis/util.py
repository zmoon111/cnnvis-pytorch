"""assigning hooks to it"""
from collections import OrderedDict
from enum import Enum, auto
from functools import partial
import torch

import torch.nn as nn
from torch.autograd import Variable


# class LayerType(Enum):
#     RELU = auto()
#     OTHER = auto()
#

# In general, the code assumes that each module is only called once

class GradType(Enum):
    # here, comments follow those in
    # <https://github.com/Lasagne/Recipes/blob/master/examples/Saliency%20Maps%20and%20Guided%20Backpropagation.ipynb>
    NAIVE = auto()  # Simonyan et al. (2013): Plain Gradient
    GUIDED = auto()  #
    DECONV = auto()


def augment_module_pre(net: nn.Module) -> (dict, list):
    callback_dict = OrderedDict()  # not necessarily ordered, but this can help some readability.

    forward_hook_remove_func_list = []

    for x, y in net.named_modules():
        if not isinstance(y, nn.Sequential) and y is not net:
            if isinstance(y, nn.ReLU):
                callback_dict[x] = {}

                def forward_hook(m, in_, out_, module_name):
                    # if callback_dict[module_name]['type'] == LayerType.RELU:
                    assert isinstance(out_, Variable)
                    assert 'output' not in callback_dict[module_name]
                    # I use Tensor so that during backwards,
                    # I don't have to think about moving numpy array to/from devices.
                    callback_dict[module_name]['output'] = out_.data.clone()
                    print(module_name, callback_dict[module_name]['output'].size())

                forward_hook_remove_func_list.append(y.register_forward_hook(partial(forward_hook, module_name=x)))

    return callback_dict, forward_hook_remove_func_list


def augment_module_post(net: nn.Module, callback_dict: dict) -> (dict, list):
    forward_hook_remove_func_list = []

    vis_param_dict = dict()
    vis_param_dict['layer'] = None
    vis_param_dict['index'] = None
    vis_param_dict['method'] = GradType.NAIVE

    for x, y in net.named_modules():
        if not isinstance(y, nn.Sequential) and y is not net:
            # I should add hook to all layers, in case they will be needed.
            def backward_hook(m: nn.Module, grad_in_, grad_out_, module_name):
                # print(module_name)
                # assert isinstance(grad_in_, tuple) and isinstance(grad_out_, tuple)
                # print('in', [z.size() if z is not None else None for z in grad_in_])
                # print('out', [z.size() if z is not None else None for z in grad_out_])

                # set grad for the layer
                (layer, index, method) = (vis_param_dict['layer'],
                                          vis_param_dict['index'],
                                          vis_param_dict['method'])
                if module_name not in callback_dict and layer != module_name:
                    # print(module_name, 'SKIP')
                    return
                #print(module_name, type(m), 'WORKING', isinstance(m, nn.Linear), isinstance(m, nn.ReLU))
                # sanity check.
                assert isinstance(grad_in_, tuple) and isinstance(grad_out_, tuple)
                # just for sanity check. I don't want to confuse Variable and Tensor.
                for z in grad_in_:
                    assert isinstance(z, Variable)
                for z in grad_out_:
                    assert isinstance(z, Variable)
                # print('in', [z.size() if z is not None else None for z in grad_in_])
                # print('out', [z.size() if z is not None else None for z in grad_out_])
                assert len(grad_out_) == 1

                # first, work on the actual grad_out.  clone for safety.
                grad_out_actual = grad_out_[0].clone()
                if layer == module_name:
                    # then hack a grad_out with 1.
                    print('change grad!')
                    #     print('in', [z.size() if z is not None else None for z in grad_in_])
                    #     print(grad_in_[0].std(), grad_in_[0].max(), grad_in_[0].min())
                    assert index is not None
                    # then you should set them.
                    grad_out_actual.data.zero_()
                    for var_ in grad_out_actual:
                        var_[index] = 1

                # then use the actual gradient is fine.
                # ok. now time to get the fake gradient.
                # first case, ReLU,
                if isinstance(m, nn.ReLU):
                    new_grad = grad_out_actual
                    # here, you need to work on
                    response = Variable(callback_dict[module_name]['output'])
                    if method == GradType.NAIVE:
                        new_grad[response <= 0] = 0
                    elif method == GradType.GUIDED:
                        new_grad[response <= 0] = 0
                        new_grad[grad_out_actual <= 0] = 0
                    elif method == GradType.DECONV:
                        new_grad[grad_out_actual <= 0] = 0
                    else:
                        raise ValueError('unsupported yet!')
                elif isinstance(m, nn.Linear):
                    w = None
                    for w in m.parameters():
                        break
                    # I think for Linear, it's always the first parameter that is the weight.
                    # should be of size output x input.
                    assert w is not None
                    # grad_in_[0] is the grad w.r.t previous layer.
                    # grad_in_[1] is the grad w.r.t weight.
                    assert tuple(w.size()) == (grad_out_actual.size()[1], grad_in_[0].size()[1]) == tuple(
                        grad_in_[1].size())
                    # then let's do multiplication myself.
                    new_grad = torch.mm(grad_out_actual, w)
                else:
                    raise TypeError('must be ReLU or Linear')
                # if layer != module_name:
                #     if isinstance(m, nn.Linear) or (isinstance(m, nn.ReLU) and method == GradType.NAIVE):
                #         # check that my gradient is computed correctly.
                #         # I will print a numerical result here, and check by eye.
                #         print((new_grad - grad_in_[0]).abs().min())
                # looks good to me.
                return (new_grad,) + grad_in_[1:]

            forward_hook_remove_func_list.append(y.register_backward_hook(partial(backward_hook, module_name=x)))

    return vis_param_dict, forward_hook_remove_func_list
