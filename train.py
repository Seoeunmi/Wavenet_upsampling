import tensorflow as tf
import denoisewavenet as wavenet
import numpy as np
import wav
import json
import customfunction as cf
import time
import datetime
import math
import os

# prevent GPU overflow
gpu_config = tf.compat.v1.ConfigProto()
gpu_config.gpu_options.allow_growth = True
# config.gpu_options.per_process_gpu_memory_fraction = 0.4
session = tf.compat.v1.InteractiveSession(config=gpu_config)


# read config file
with open("config.json", "r") as f_json:
    config = json.load(f_json)

default_float = config['default_float']
previous_size = config['previous_size']
current_size = config['current_size']
future_size = config['future_size']
receptive_size = previous_size + current_size + future_size
shift_size = config['shift_size']
batch_size = config['batch_size']
epochs = config['epochs']
training_target_path = config['training_target_path']
training_source_path = config['training_source_path']
save_check_point_name = config['save_check_point_name']
load_check_point_name = config['load_check_point_name']

# training_target_path is path or file?
target_path_isdir = os.path.isdir(training_target_path)
source_path_isdir = os.path.isdir(training_source_path)
if target_path_isdir != source_path_isdir:
    raise Exception("ERROR: Target and source path is incorrect")
if target_path_isdir:
    if not cf.compare_path_list(training_target_path, training_source_path, 'wav'):
        raise Exception("ERROR: Target and source file list is not same")
    training_target_file_list = cf.read_path_list(training_target_path, "wav")
    training_source_file_list = cf.read_path_list(training_source_path, "wav")
else:
    training_target_file_list = [training_target_path]
    training_source_file_list = [training_source_path]


x_signal, y_signal = [], []
num_of_total_frame = 0
for i in range(len(training_target_file_list)):
    # read train data file
    target_signal, target_sample_rate = wav.read_wav(training_target_file_list[i])
    source_signal, source_sample_rate = wav.read_wav(training_source_file_list[i])

    source_signal = cf.create_signal(source_signal, config['upsample_size'])
    if len(source_signal) != len(target_signal):
        source_signal = source_signal[:len(target_signal)]

    target_signal = np.array(target_signal)
    source_signal = np.array(source_signal)
    size_of_target = target_signal.size
    size_of_source = source_signal.size

    # source & target file incorrect
    if size_of_source != size_of_target:
        raise Exception("ERROR: Input, output size mismatch")
    if size_of_source < current_size:
        raise Exception("ERROR: Input file length is too small")
    if shift_size <= 0:
        raise Exception("ERROR: Shift size is smaller or same with 0")

    # padding
    mod = (shift_size - (size_of_source % shift_size)) % shift_size
    target_signal_padded = np.concatenate([np.zeros(previous_size), target_signal, np.zeros(future_size+mod)]).astype(default_float)
    source_signal_padded = np.concatenate([np.zeros(previous_size), source_signal, np.zeros(future_size+mod)]).astype(default_float)
    if shift_size < current_size:
        dif = current_size-shift_size
        target_signal_padded = np.concatenate([target_signal_padded, np.zeros(dif)]).astype(default_float)
        source_signal_padded = np.concatenate([source_signal_padded, np.zeros(dif)]).astype(default_float)

    # make dataset
    number_of_frames = math.ceil(size_of_source/shift_size)
    num_of_total_frame += number_of_frames
    for j in range(number_of_frames):
        x_signal.append(source_signal_padded[j*shift_size:(j*shift_size) + receptive_size])
        y_signal.append(target_signal_padded[j*shift_size:(j*shift_size) + receptive_size])

# train_dataset = tf.data.Dataset.from_tensor_slices((x_signal, y_signal))
train_dataset = tf.data.Dataset.from_tensor_slices((x_signal, y_signal)).shuffle(num_of_total_frame).batch(batch_size)

# make model
model = wavenet.DenoiseWaveNet(config['dilation'], config['relu_alpha'], config['default_float'])
loss_object = tf.keras.losses.MeanAbsoluteError()
optimizer = tf.keras.optimizers.Adam(learning_rate=config['learning_rate'])
train_loss = tf.keras.metrics.Mean(name='train_loss')

# train function
@tf.function
def train_step(x, y):
    y_true = tf.squeeze(y)
    if len(y_true.shape) == 2:
        start = [0, previous_size]
        size = [-1, current_size]
    elif len(y_true.shape) == 1:
        start = [previous_size]
        size = [current_size]
    with tf.GradientTape() as tape:
        y_pred = model(x)
        loss = loss_object(tf.slice(y_true, start, size), tf.slice(y_pred, start, size))*2
    gradients = tape.gradient(loss, model.trainable_variables)
    optimizer.apply_gradients(zip(gradients, model.trainable_variables))
    train_loss(loss)

# load model
if load_check_point_name != "":
    saved_epoch = int(load_check_point_name.split('_')[-1])
    for x, y in train_dataset:
        train_step(x, y)
        break
    model.load_weights('{}/checkpoint/{}/data.ckpt'.format(cf.load_path(), load_check_point_name))
    model.load_optimizer_state(optimizer, '{}/checkpoint/{}'.format(cf.load_path(), load_check_point_name), 'optimizer')
    train_loss.reset_states()
else:
    cf.clear_plot_file('{}/{}'.format(cf.load_path(), config['plot_file']))
    saved_epoch = 0

# train run
for epoch in range(saved_epoch, saved_epoch+epochs):
    i = 0
    start = time.time()
    for x, y in train_dataset:
        print("\rTrain : epoch {}/{}, training {}/{}".format(epoch + 1, saved_epoch+epochs, i + 1, math.ceil(num_of_total_frame / batch_size)), end='')
        train_step(x, y)
        i += 1
    print(" | loss : {}".format(train_loss.result()), " | Processing time :", datetime.timedelta(seconds=time.time() - start))

    if ((epoch + 1) % config['save_check_point_period'] == 0) or (epoch + 1 == 1):
        cf.createFolder("{}/checkpoint/{}_{}".format(cf.load_path(), save_check_point_name, epoch+1))
        model.save_weights('{}/checkpoint/{}_{}/data.ckpt'.format(cf.load_path(), save_check_point_name, epoch+1))
        model.save_optimizer_state(optimizer, '{}/checkpoint/{}_{}'.format(cf.load_path(), save_check_point_name, epoch+1), 'optimizer')

    # write plot file
    cf.write_plot_file('{}/{}'.format(cf.load_path(), config['plot_file']), epoch+1, train_loss.result())
    train_loss.reset_states()