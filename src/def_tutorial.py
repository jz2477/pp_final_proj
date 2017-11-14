from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import pp.edward as ed  # Importing my own version of Edward
import numpy as np
import os
import tensorflow as tf

from datetime import datetime
from pp.edward.models import Gamma, Poisson, Normal, PointMass, \
    TransformedDistribution
from pp.edward.util import Progbar
from observations import nips

ed.set_seed(42)

data_dir = "~/data"
logdir = '~/log/def/'
data_dir = os.path.expanduser(data_dir)
logdir = os.path.expanduser(logdir)

# DATA
x_train, metadata = nips(data_dir)
documents = metadata['columns']
words = metadata['rows']

# Subset to documents in 2011 and words appearing in at least two
# documents and have a total word count of at least 10.
doc_idx = [i for i, document in enumerate(documents)
           if document.startswith('2011')]
documents = [documents[doc] for doc in doc_idx]
x_train = x_train[:, doc_idx]
word_idx = np.logical_and(np.sum(x_train != 0, 1) >= 2,
                          np.sum(x_train, 1) >= 10)
words = [word for word, idx in zip(words, word_idx) if idx]
x_train = x_train[word_idx, :]
x_train = x_train.T

N = x_train.shape[0]  # number of documents
D = x_train.shape[1]  # vocabulary size
K = [100]
#K = [100, 30, 15]  # number of components per layer
q = 'gamma'  # choice of q; 'lognormal' or 'gamma'
shape = 0.1  # gamma shape parameter
lr = 1e-4  # learning rate step-size

# MODEL
#W2 = Gamma(0.1, 0.3, sample_shape=[K[2], K[1]])
#W1 = Gamma(0.1, 0.3, sample_shape=[K[1], K[0]])
W0 = Gamma(0.1, 0.3, sample_shape=[K[0], D])

#z3 = Gamma(0.1, 0.1, sample_shape=[N, K[2]])
#z2 = Gamma(shape, shape / tf.matmul(z3, W2))
#z1 = Gamma(shape, shape / tf.matmul(z2, W1))
z1 = Gamma(0.1, 0.1, sample_shape=[N, K[0]])
x = Poisson(tf.matmul(z1, W0))


# INFERENCE
def pointmass_q(shape):
  min_mean = 1e-3
  mean_init = tf.random_normal(shape)
  rv = PointMass(tf.maximum(tf.nn.softplus(tf.Variable(mean_init)), min_mean))
  return rv


def gamma_q(shape):
  # Parameterize Gamma q's via shape and scale, with softplus unconstraints.
  min_shape = 1e-3
  min_scale = 1e-5
  shape_init = 0.5 + 0.1 * tf.random_normal(shape)
  scale_init = 0.1 * tf.random_normal(shape)
  rv = Gamma(tf.maximum(tf.nn.softplus(tf.Variable(shape_init)),
                        min_shape),
             tf.maximum(1.0 / tf.nn.softplus(tf.Variable(scale_init)),
                        1.0 / min_scale))
  return rv


def lognormal_q(shape):
  min_scale = 1e-5
  loc_init = tf.random_normal(shape)
  scale_init = 0.1 * tf.random_normal(shape)
  rv = TransformedDistribution(
      distribution=Normal(
          tf.Variable(loc_init),
          tf.maximum(tf.nn.softplus(tf.Variable(scale_init)), min_scale)),
      bijector=tf.contrib.distributions.bijectors.Exp())
  return rv


#qW2 = pointmass_q(W2.shape)
#qW1 = pointmass_q(W1.shape)
qW0 = pointmass_q(W0.shape)
if q == 'gamma':
#  qz3 = gamma_q(z3.shape)
#  qz2 = gamma_q(z2.shape)
  qz1 = gamma_q(z1.shape)
else:
#  qz3 = lognormal_q(z3.shape)
#  qz2 = lognormal_q(z2.shape)
  qz1 = lognormal_q(z1.shape)

# We apply variational EM with E-step over local variables
# and M-step to point estimate the global weight matrices.
inference_e = ed.KLqp({z1: qz1},#, z2: qz2, z3: qz3},
                      data={x: x_train, W0: qW0})#, W1: qW1, W2: qW2})
inference_m = ed.MAP({W0: qW0}, #, W1: qW1, W2: qW2},
                     data={x: x_train, z1: qz1})#, z2: qz2, z3: qz3})

optimizer_e = tf.train.RMSPropOptimizer(lr)
optimizer_m = tf.train.RMSPropOptimizer(lr)
timestamp = datetime.strftime(datetime.utcnow(), "%Y%m%d_%H%M%S")
logdir += timestamp + '_' + '_'.join([str(ks) for ks in K]) + \
    '_q_' + str(q) + '_lr_' + str(lr)
kwargs = {'optimizer': optimizer_e,
          'n_print': 100,
          'logdir': logdir,
          'log_timestamp': False}

if q == 'gamma':
  kwargs['n_samples'] = 30
inference_e.initialize(**kwargs)
inference_m.initialize(optimizer=optimizer_m)

sess = ed.get_session()
tf.global_variables_initializer().run()

n_epoch = 10 # Change to 20
n_iter_per_epoch = 10000
print("Log directory: ", logdir)
for epoch in range(n_epoch):
  print("Epoch {}".format(epoch))
  nll = 0.0

  pbar = Progbar(n_iter_per_epoch)
  for t in range(1, n_iter_per_epoch + 1):
    pbar.update(t)
    info_dict_e = inference_e.update()
    info_dict_m = inference_m.update()
    nll += info_dict_e['loss']

  # Compute perplexity averaged over a number of training iterations.
  # The model's negative log-likelihood of data is upper bounded by
  # the variational objective.
  nll = nll / n_iter_per_epoch
  perplexity = np.exp(nll / np.sum(x_train))
  print("Negative log-likelihood <= {:1.3f}".format(nll))
  print("Perplexity <= {:0.3f}".format(perplexity))

  # Print top 10 words for first 10 topics.
  qW0_vals = sess.run(qW0)
  for k in range(10):
    top_words_idx = qW0_vals[k, :].argsort()[-10:][::-1]
    top_words = " ".join([words[i] for i in top_words_idx])
    print("Topic {}: {}".format(k, top_words))

print("Log directory: ", logdir)
