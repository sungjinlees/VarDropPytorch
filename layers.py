import torch
from torch import nn
from torch.nn import Parameter
import torch.nn.functional as F
from functools import reduce
import operator

class LinearARD(nn.Module):
    """
    Dense layer implementation with weights ARD-prior (arxiv:1701.05369)
    """

    def __init__(self, in_features, out_features, bias=True):
        super(LinearARD, self).__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.weight = Parameter(torch.Tensor(out_features, in_features))
        if bias:
            self.bias = Parameter(torch.Tensor(out_features))
        else:
            self.register_parameter('bias', None)
        self.log_sigma2 = Parameter(torch.Tensor(out_features, in_features))
        self.reset_parameters()

    def reset_parameters(self):
        self.weight.data.normal_(std=0.01)
        if self.bias is not None:
            self.bias.data.uniform_(0, 0)
        self.log_sigma2.data.uniform_(-10,-10)

    @staticmethod
    def clip(tensor, to=8):
        """
        Shrink all tensor's values to range [-to,to]
        """
        return torch.clamp(tensor, -to, to)

    def forward(self, input):
        """
        Forward with all regularized connections and random activations (Beyesian mode). Typically used for train
        """
        return self._forward(input)

    def forward_w_clip(self, input, thresh=3):
        """
        Forward with dropped unsignificant connections and random activations (Bayesian mode)

        :param input - input Tensor
        :param thresh - all weights greater "thresh" parameter will be dropped (unsignificant connections)
        """
        return self._forward(input, clip=True, thresh=thresh)

    def forward_deterministic(self, input, thresh=3):
        """
        Forward with dropped unsignificant connections with deterministic weights. Typically used in test.
        Without regularization and high enough "thresh" parameter (>= 3) it's mode equivalent to simle nn.Linear layer

        :param input - input Tensor
        :param thresh - all weights greater "thresh" parameter will be dropped (unsignificant connections)
        """
        return self._forward(input, deterministic=True, thresh=thresh)

    def _forward(self, input, clip=False, deterministic=False, thresh=3):
        log_alpha = self.clip(self.log_sigma2 - torch.log(self.weight ** 2))
        clip_mask = torch.ge(log_alpha, thresh)
        W = self.weight
        zeros = torch.zeros_like(W)
        if deterministic:
            activation = input.matmul(torch.where(clip_mask, zeros, self.weight).t())
        else:
            if clip:
                W = torch.where(clip_mask, zeros, self.weight)
            mu = input.matmul(W.t())
            si = torch.sqrt((input * input)\
                .matmul(((torch.exp(log_alpha) * self.weight * self.weight)+1e-8).t()))
            activation = mu + torch.normal(torch.zeros_like(mu), torch.ones_like(mu)) * si
        return activation + self.bias


    def eval_reg(self, **kwargs):
        """
        Get weights regularization (KL(q(w)||p(w)) approximation)
        """
        k1, k2, k3 = 0.63576, 1.8732, 1.48695; C = -k1
        log_alpha = self.clip(self.log_sigma2 - torch.log(self.weight ** 2))
        mdkl = k1 * torch.sigmoid(k2 + k3 * log_alpha) - 0.5 * torch.log1p(torch.exp(-log_alpha)) + C
        return -torch.sum(mdkl)

    def extra_repr(self):
        return 'in_features={}, out_features={}, bias={}'.format(
            self.in_features, self.out_features, self.bias is not None
        )

    def get_ard(self, thresh=3, **kwargs):
        """
        Get number of dropped weights (greater than "thresh" parameter)

        :returns (number of dropped weights, number of all weight)
        """
        log_alpha = self.log_sigma2 - 2 * torch.log(torch.abs(self.weight))
        params_cnt_dropped = int((log_alpha > thresh).sum().cpu().numpy())
        params_cnt_all = reduce(operator.mul, log_alpha.shape, 1)
        return params_cnt_dropped, params_cnt_all

    def get_reg(self):
        log_alpha = self.log_sigma2 - 2 * torch.log(torch.abs(self.weight))
        return log_alpha.min(), log_alpha.max()

class Conv2dARD(nn.Conv2d):
    def __init__(self, in_channels, out_channels, kernel_size, stride=1,
                 padding=0, dilation=1, groups=1, ard_init=-10):
        bias = None # Learnable bias is not implemented yet
        super(Conv2dARD, self).__init__(in_channels, out_channels, kernel_size, stride,
                     padding, dilation, groups, bias)
        self.ard_init = ard_init
        self.log_sigma2 = Parameter(ard_init*torch.ones_like(self.weight))

    @staticmethod
    def clip(tensor, to=8):
        """
        Shrink all tensor's values to range [-to,to]
        """
        return torch.clamp(tensor, -to, to)

    def forward(self, input):
        """
        Forward with all regularized connections and random activations (Beyesian mode). Typically used for train
        """
        return self._forward(input)

    def forward_w_clip(self, input, thresh=3):
        """
        Forward with dropped unsignificant connections and random activations (Bayesian mode)

        :param input - input Tensor
        :param thresh - all weights greater "thresh" parameter will be dropped (unsignificant connections)
        """
        return self._forward(input, clip=True, thresh=thresh)

    def forward_deterministic(self, input, thresh=3):
        """
        Forward with dropped unsignificant connections with deterministic weights. Typically used in test.
        Without regularization and high enough "thresh" parameter (>= 3) it's mode equivalent to simle nn.Linear layer

        :param input - input Tensor
        :param thresh - all weights greater "thresh" parameter will be dropped (unsignificant connections)
        """
        return self._forward(input, deterministic=True, thresh=thresh)


    def _forward(self, input, clip=False, deterministic=False, thresh=3):
        log_alpha = self.clip(self.log_sigma2 - torch.log(self.weight ** 2 + 1e-8))
        clip_mask = torch.ge(log_alpha, thresh)
        W = self.weight
        zeros = torch.zeros_like(W)
        if deterministic:
            conved = F.conv2d(input, torch.where(clip_mask, zeros, self.weight),
                self.bias, self.stride,
                self.padding, self.dilation, self.groups)
        else:
            if clip:
                W = torch.where(clip_mask, zeros, W)
            conved_mu = F.conv2d(input, W, self.bias, self.stride,
                self.padding, self.dilation, self.groups)
            conved_si = torch.sqrt(1e-8 + F.conv2d(input*input,
                torch.exp(log_alpha) * W * W, self.bias, self.stride,
                self.padding, self.dilation, self.groups))
            conved = conved_mu + \
                conved_si * torch.normal(torch.zeros_like(conved_mu), torch.ones_like(conved_mu))
        return conved

    def eval_reg(self, **kwargs):
        """
        Get weights regularization (KL(q(w)||p(w)) approximation)
        """
        k1, k2, k3 = 0.63576, 1.8732, 1.48695; C = -k1
        log_alpha = self.clip(self.log_sigma2 - torch.log(self.weight ** 2))
        mdkl = k1 * torch.sigmoid(k2 + k3 * log_alpha) - 0.5 * torch.log1p(torch.exp(-log_alpha)) + C
        return -torch.sum(mdkl)

    def extra_repr(self):
        return 'in_features={}, out_features={}, bias={}'.format(
            self.in_features, self.out_features, self.bias is not None
        )

    def get_ard(self, thresh=3, **kwargs):
        """
        Get number of dropped weights (greater than "thresh" parameter)

        :returns (number of dropped weights, number of all weight)
        """
        log_alpha = self.log_sigma2 - 2 * torch.log(torch.abs(self.weight))
        params_cnt_dropped = int((log_alpha > thresh).sum().cpu().numpy())
        params_cnt_all = reduce(operator.mul, log_alpha.shape, 1)
        return params_cnt_dropped, params_cnt_all

    def get_reg(self):
        log_alpha = self.log_sigma2 - 2 * torch.log(torch.abs(self.weight))
        return log_alpha.min(), log_alpha.max()
