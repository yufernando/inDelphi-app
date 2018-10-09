from __future__ import division
import numpy as np
import pandas as pd
import os
from collections import defaultdict
import pickle, copy
from scipy.stats import entropy

init_flag = False
nn_params = None
nn2_params = None
normalizer = dict()
rate_model = dict()
bp_model = dict()

##
# Private NN methods
##
def __sigmoid(x):
  return 0.5 * (np.tanh(x) + 1.0)

def __nn_function(params, inputs):
  # """Params is a list of (weights, bias) tuples.
  #    inputs is an (N x D) matrix."""
  inpW, inpb = params[0]
  inputs = __sigmoid(np.dot(inputs, inpW) + inpb)
  for W, b in params[1:-1]:
    outputs = np.dot(inputs, W) + b
    inputs = __sigmoid(outputs)
  outW, outb = params[-1]
  outputs = np.dot(inputs, outW) + outb
  return outputs.flatten()

##
# Private sequence featurization
##
def __get_gc_frac(seq):
  return (seq.count('C') + seq.count('G')) / len(seq)

def __find_microhomologies(left, right):
  start_idx = max(len(right) - len(left), 0)
  mhs = []
  mh = [start_idx]
  for idx in range(min(len(right), len(left))):
    if left[idx] == right[start_idx + idx]:
      mh.append(start_idx + idx + 1)
    else:
      mhs.append(mh)
      mh = [start_idx + idx +1]
  mhs.append(mh)
  return mhs

def __featurize(seq, cutsite, DELLEN_LIMIT = 60):
  # print('Using DELLEN_LIMIT = %s' % (DELLEN_LIMIT))
  mh_lens, gc_fracs, gt_poss, del_lens = [], [], [], []
  for del_len in range(1, DELLEN_LIMIT):
    left = seq[cutsite - del_len : cutsite]
    right = seq[cutsite : cutsite + del_len]
    if len(left) != len(right):
      break

    mhs = __find_microhomologies(left, right)
    for mh in mhs:
      mh_len = len(mh) - 1
      if mh_len > 0:
        gtpos = max(mh)
        gt_poss.append(gtpos)

        s = cutsite - del_len + gtpos - mh_len
        e = s + mh_len
        mh_seq = seq[s : e]
        gc_frac = __get_gc_frac(mh_seq)

        mh_lens.append(mh_len)
        gc_fracs.append(gc_frac)
        del_lens.append(del_len)

  return mh_lens, gc_fracs, gt_poss, del_lens

##
# Error catching
##
def error_catching(seq, cutsite):
  # Type errors
  if type(seq) != str:
    return True, 'Sequence is not a string.'
  if type(cutsite) != int:
    return True, 'Cutsite is not an int.'
  
  # Cutsite bounds errors
  if cutsite < 1 or cutsite > len(seq) - 1:
    return True, 'Cutsite index is not within the sequence. Cutsite must be an integer between index 1 and len(seq) - 1, inclusive.'

  # Sequence string errors
  for c in set(seq):
    if c not in list('ACGT'):
      return True, 'Only ACGT characters allowed: Bad character %s' % (c)


  return False, ''

def provide_warnings(seq, cutsite):
  if len(seq) <= 10:
    print('Warning: Sequence length is very short (%s bp)' % (len(seq)))
  return

##
# Private prediction methods
##
def __predict_dels(seq, cutsite):
  ################################################################
  #####
  ##### Predict MH and MH-less deletions
  #####
  # Predict MH deletions

  mh_len, gc_frac, gt_pos, del_len = __featurize(seq, cutsite)

  # Form inputs
  pred_input = np.array([mh_len, gc_frac]).T
  del_lens = np.array(del_len).T
  
  # Predict
  mh_scores = __nn_function(nn_params, pred_input)
  mh_scores = mh_scores.reshape(mh_scores.shape[0], 1)
  Js = del_lens.reshape(del_lens.shape[0], 1)
  unfq = np.exp(mh_scores - 0.25*Js)

  # Add MH-less contribution at full MH deletion lengths
  mh_vector = np.array(mh_len)
  mhfull_contribution = np.zeros(mh_vector.shape)
  for jdx in range(len(mh_vector)):
    if del_lens[jdx] == mh_vector[jdx]:
      dl = del_lens[jdx]
      mhless_score = __nn_function(nn2_params, np.array(dl))
      mhless_score = np.exp(mhless_score - 0.25*dl)
      mask = np.concatenate([np.zeros(jdx,), np.ones(1,) * mhless_score, np.zeros(len(mh_vector) - jdx - 1,)])
      mhfull_contribution = mhfull_contribution + mask
  mhfull_contribution = mhfull_contribution.reshape(-1, 1)
  unfq = unfq + mhfull_contribution

  # Store predictions to combine with mh-less deletion preds
  pred_del_len = copy.copy(del_len)
  pred_gt_pos = copy.copy(gt_pos)

  ################################################################
  #####
  ##### Predict MH and MH-less deletions
  #####
  # Predict MH-less deletions
  mh_len, gc_frac, gt_pos, del_len = __featurize(seq, cutsite)

  unfq = list(unfq)

  pred_mhless_d = defaultdict(list)
  # Include MH-less contributions at non-full MH deletion lengths
  nonfull_dls = []
  for dl in range(1, 60):
    if dl not in del_len:
      nonfull_dls.append(dl)
    elif del_len.count(dl) == 1:
      idx = del_len.index(dl)
      if mh_len[idx] != dl:
        nonfull_dls.append(dl)
    else:
        nonfull_dls.append(dl)

  mh_vector = np.array(mh_len)
  for dl in nonfull_dls:
    mhless_score = __nn_function(nn2_params, np.array(dl))
    mhless_score = np.exp(mhless_score - 0.25*dl)

    unfq.append(mhless_score)
    pred_gt_pos.append('e')
    pred_del_len.append(dl)

  unfq = np.array(unfq)
  total_phi_score = float(sum(unfq))

  nfq = np.divide(unfq, np.sum(unfq))  
  pred_freq = list(nfq.flatten())

  d = {'Length': pred_del_len, 'Genotype position': pred_gt_pos, 'Predicted frequency': pred_freq}
  pred_del_df = pd.DataFrame(d)
  pred_del_df['Category'] = 'del'
  return pred_del_df, total_phi_score

def __predict_ins(seq, cutsite, pred_del_df, total_phi_score, celltype):
  ################################################################
  #####
  ##### Predict Insertions
  #####
  # Predict 1 bp insertions
  dlpred = []
  for dl in range(1, 28+1):
    crit = (pred_del_df['Length'] == dl)
    dlpred.append(sum(pred_del_df[crit]['Predicted frequency']))
  dlpred = np.array(dlpred) / sum(dlpred)
  norm_entropy = entropy(dlpred) / np.log(len(dlpred))
  precision = 1 - norm_entropy
  log_phi_score = np.log(total_phi_score)

  fiveohmapper = {'A': [1, 0, 0, 0], 
                  'C': [0, 1, 0, 0], 
                  'G': [0, 0, 1, 0], 
                  'T': [0, 0, 0, 1]}
  threeohmapper = {'A': [1, 0, 0, 0], 
                   'C': [0, 1, 0, 0], 
                   'G': [0, 0, 1, 0], 
                   'T': [0, 0, 0, 1]}
  fivebase = seq[cutsite - 1]
  threebase = seq[cutsite]
  onebp_features = fiveohmapper[fivebase] + threeohmapper[threebase] + [precision] + [log_phi_score]
  for idx in range(len(onebp_features)):
    val = onebp_features[idx]
    onebp_features[idx] = (val - normalizer[celltype][idx][0]) / normalizer[celltype][idx][1]
  onebp_features = np.array(onebp_features).reshape(1, -1)
  rate_1bpins = float(rate_model[celltype].predict(onebp_features))

  # Predict 1 bp genotype frequencies
  pred_1bpins_d = defaultdict(list)
  negfivebase = seq[cutsite - 2]
  negfourbase = seq[cutsite - 1]
  negthreebase = seq[cutsite]

  if celltype in ['mESC', 'U2OS']:
    for ins_base in bp_model[celltype][negfivebase][negfourbase][negthreebase]:
      freq = bp_model[celltype][negfivebase][negfourbase][negthreebase][ins_base]
      freq *= rate_1bpins / (1 - rate_1bpins)
      pred_1bpins_d['Category'].append('ins')
      pred_1bpins_d['Length'].append(1)
      pred_1bpins_d['Inserted Bases'].append(ins_base)
      pred_1bpins_d['Predicted frequency'].append(freq)
  elif celltype in ['K562', 'HEK293', 'HCT116']:
    for ins_base in bp_model[celltype][negfivebase]:
      freq = bp_model[celltype][negfivebase][ins_base]
      freq *= rate_1bpins / (1 - rate_1bpins)
      pred_1bpins_d['Category'].append('ins')
      pred_1bpins_d['Length'].append(1)
      pred_1bpins_d['Inserted Bases'].append(ins_base)
      pred_1bpins_d['Predicted frequency'].append(freq)

  pred_1bpins_df = pd.DataFrame(pred_1bpins_d)
  pred_df = pred_del_df.append(pred_1bpins_df, ignore_index = True)
  pred_df['Predicted frequency'] /= sum(pred_df['Predicted frequency'])
  return pred_df

def __build_stats(seq, cutsite, pred_df, total_phi_score, celltype):
  # Precision stats
  overall_precision = 1 - entropy(pred_df['Predicted frequency']) / np.log(len(pred_df))
  highest_fq = max(pred_df['Predicted frequency'])
  highest_del_fq = max(pred_df[pred_df['Category'] == 'del']['Predicted frequency'])
  highest_ins_fq = max(pred_df[pred_df['Category'] == 'ins']['Predicted frequency'])
  

  # Outcomes
  ins_fq = sum(pred_df[pred_df['Category'] == 'ins']['Predicted frequency'])
  crit = (pred_df['Category'] == 'del') & (pred_df['Genotype position'] != 'e')
  mhdel_fq = sum(pred_df[crit]['Predicted frequency'])

  crit = (pred_df['Category'] == 'del') & (pred_df['Genotype position'] == 'e')
  nomhdel_fq = sum(pred_df[crit]['Predicted frequency'])

  # Expected indel length
  ddf = pred_df[pred_df['Category'] == 'del']
  expected_indel_len = sum(ddf['Predicted frequency'] * ddf['Length'] / 100)
  idf = pred_df[pred_df['Category'] == 'ins']
  expected_indel_len += sum(idf['Predicted frequency'] * idf['Length'] / 100)

  # Frameshifts
  fsd = {'+0': 0, '+1': 0, '+2': 0}

  crit = (pred_df['Category'] == 'ins')
  ins1_fq = sum(pred_df[crit]['Predicted frequency'])
  fsd['+1'] += ins1_fq

  for del_len in set(pred_df['Length']):
    crit = (pred_df['Category'] == 'del') & (pred_df['Length'] == del_len)
    fq = sum(pred_df[crit]['Predicted frequency'])
    fs = (-1 * del_len) % 3
    fsd['+%s' % (fs)] += fq

  stats = {'Phi': total_phi_score,
           'Precision': overall_precision,
           '1-bp ins frequency': ins_fq,
           'MH del frequency': mhdel_fq,
           'MHless del frequency': nomhdel_fq,
           'Frameshift frequency': fsd['+1'] + fsd['+2'],
           'Frame +0 frequency': fsd['+0'], 
           'Frame +1 frequency': fsd['+1'], 
           'Frame +2 frequency': fsd['+2'], 
           'Highest outcome frequency': highest_fq,
           'Highest del frequency': highest_del_fq,
           'Highest ins frequency': highest_ins_fq,
           'Expected indel length': expected_indel_len,
           'Reference sequence': seq,
           'Cutsite': cutsite,
           'gRNA': seq[cutsite - 18 : cutsite + 3],
           'gRNA orientation': '+',
           'Celltype': celltype,
          }
  return stats

##
# Main public-facing prediction
##
def predict(seq, cutsite, celltype):
  # Predict 1 bp insertions and all deletions (MH and MH-less)
  #
  # If no errors, returns a tuple (pred_df, stats)
  # where pred_df is a dataframe and stats is a dict
  #  
  # If errors, returns a string
  #
  if init_flag == False:
    init_model(celltype = celltype)

  # Sanitize input
  seq = seq.upper()
  flag, error = error_catching(seq, cutsite)
  if flag:
    return error
  provide_warnings(seq, cutsite)


  # Make predictions
  pred_del_df, total_phi_score = __predict_dels(seq, cutsite)
  pred_df = __predict_ins(seq, cutsite, pred_del_df, total_phi_score, celltype)
  pred_df['Predicted frequency'] *= 100

  # Build stats
  stats = __build_stats(seq, cutsite, pred_df, total_phi_score, celltype)
  
  return pred_df, stats

##
# Process predictions
##
def get_frameshift_fqs(pred_df):
  # Returns a dataframe
  #   - Frame
  #   - Predicted frequency
  #
  fsd = {'+0': 0, '+1': 0, '+2': 0}

  crit = (pred_df['Category'] == 'ins')
  ins1_fq = sum(pred_df[crit]['Predicted frequency'])
  fsd['+1'] += ins1_fq

  for del_len in set(pred_df['Length']):
    crit = (pred_df['Category'] == 'del') & (pred_df['Length'] == del_len)
    fq = sum(pred_df[crit]['Predicted frequency'])
    fs = (-1 * del_len) % 3
    fsd['+%s' % (fs)] += fq

  d = defaultdict(list)
  d['Frame'] = list(fsd.keys())
  d['Predicted frequency'] = list(fsd.values())
  df = pd.DataFrame(d)
  df = df.sort_values(by = 'Frame')
  df = df.reset_index(drop = True)
  return df

def get_indel_length_fqs(pred_df):
  # Returns a dataframe
  #   - Indel length
  #   - Predicted frequency
  d = defaultdict(list)

  crit = (pred_df['Category'] == 'ins')
  ins1_fq = sum(pred_df[crit]['Predicted frequency'])
  d['Indel length'].append('+1')
  d['Predicted frequency'].append(ins1_fq)

  for del_len in set(pred_df['Length']):
    crit = (pred_df['Category'] == 'del') & (pred_df['Length'] == del_len)
    fq = sum(pred_df[crit]['Predicted frequency'])
    d['Indel length'].append('-%s' % (del_len))
    d['Predicted frequency'].append(fq)

  df = pd.DataFrame(d)
  return df  

def get_indel_length_breakdown(pred_df):
  # Returns a dataframe
  #   - Indel length
  #   - Predicted frequency
  d = defaultdict(list)

  for ins_base in list('ACGT'):
    crit = (pred_df['Category'] == 'ins') & (pred_df['Inserted Bases'] == ins_base)
    ins1_fq = sum(pred_df[crit]['Predicted frequency'])
    d['Indel length'].append('+1')
    d['Predicted frequency'].append(ins1_fq)
    d['Detail'].append(ins_base)

  for del_len in set(pred_df['Length']):
    crit = (pred_df['Category'] == 'del') & (pred_df['Length'] == del_len) & (pred_df['Genotype position'] == 'e')
    fq = sum(pred_df[crit]['Predicted frequency'])
    d['Indel length'].append('-%s' % (del_len))
    d['Predicted frequency'].append(fq)
    d['Detail'].append('MH-less')

    crit = (pred_df['Category'] == 'del') & (pred_df['Length'] == del_len) & (pred_df['Genotype position'] != 'e')
    fq = sum(pred_df[crit]['Predicted frequency'])
    d['Indel length'].append('-%s' % (del_len))
    d['Predicted frequency'].append(fq)
    d['Detail'].append('Microhomology')

  df = pd.DataFrame(d)
  return df  

def get_highest_frequency_indel(pred_df):
  # Returns a row of pred_df
  highest_fq = max(pred_df['Predicted frequency'])
  row = pred_df[pred_df['Predicted frequency'] == highest_fq]
  return row.iloc[0]

def get_highest_frequency_length(pred_df):
  idd = get_indel_length_fqs(pred_df)
  highest_fq = max(idd['Predicted frequency'])
  row = idd[idd['Predicted frequency'] == highest_fq]
  return row.iloc[0]

def get_precision(pred_df):
  # Returns a row of pred_df
  return 1 - entropy(pred_df['Predicted frequency']) / np.log(len(pred_df))

##
# Data reformatting
##
def add_genotype_column(pred_df, stats):
  if 'Genotype' in pred_df.columns:
    return
  gts = []
  if type(stats) == pd.DataFrame:
    seq = stats['Reference sequence'].iloc[0]
    cutsite = stats['Cutsite'].iloc[0]
  elif type(stats) == dict:
    seq = stats['Reference sequence']
    cutsite = stats['Cutsite']

  for idx, row in pred_df.iterrows():
    gt_pos = row['Genotype position']
    if gt_pos == 'e':
      gt = np.nan
    elif row['Category'] == 'del':
      dl = row['Length']
      gt_pos = int(gt_pos)
      gt = seq[:cutsite - dl + gt_pos] + seq[cutsite + gt_pos:]
    else:
      ins_base = row['Inserted Bases']
      gt = seq[:cutsite] + ins_base + seq[cutsite:]
    gts.append(gt)
  pred_df['Genotype'] = gts
  return

def add_name_column(pred_df, stats):
  names = []
  seq = stats['Reference sequence'].iloc[0]
  cutsite = stats['Cutsite'].iloc[0]

  for idx, row in pred_df.iterrows():
    gt_pos = row['Genotype position']
    if gt_pos == 'e':
      name = 'del%s' % (row['Length'])
    elif row['Category'] == 'del':
      dl = row['Length']
      gt_pos = int(gt_pos)
      name = 'del%s' % (seq[cutsite - dl + gt_pos : cutsite + gt_pos])
    else:
      ins_base = row['Inserted Bases']
      name = 'ins%s' % (ins_base)
    names.append(name)
  pred_df['Name'] = names
  return



##
# Init
##
def init_model(run_iter = 'aax', 
               param_iter = 'aag'):
  global init_flag
  if init_flag != False:
    return

  print('Initializing models %s/%s...' % (run_iter, param_iter))

  model_dir = os.path.dirname(os.path.realpath(__file__))
  model_dir += '/model'

  global nn_params
  global nn2_params
  with open('%s/%s_%s_nn.pkl' % (model_dir, run_iter, param_iter), 'rb') as f:
    # load in python3.6 a pickle that was dumped from python2.7
    nn_params = pickle.load(f, encoding = 'latin1')
  with open('%s/%s_%s_nn2.pkl' % (model_dir, run_iter, param_iter), 'rb') as f:
    nn2_params = pickle.load(f, encoding = 'latin1')

  global normalizer
  global rate_model
  global bp_model
  for celltype in ['mESC', 'U2OS', 'HEK293', 'HCT116', 'K562']:
    with open('%s/bp_model_%s.pkl' % (model_dir, celltype), 'rb') as f:
      bp_model[celltype] = pickle.load(f, encoding = 'latin1')
    with open('%s/rate_model_%s.pkl' % (model_dir, celltype), 'rb') as f:
      rate_model[celltype] = pickle.load(f, encoding = 'latin1')
    with open('%s/Normalizer_%s.pkl' % (model_dir, celltype), 'rb') as f:
      normalizer[celltype] = pickle.load(f, encoding = 'latin1')

  init_flag = True

  print('Done')
  return

