import pyaudio
import numpy as np


def play_shit(samples, fs=44100, volume=0.5):
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paFloat32,
                    channels=1,
                    rate=fs,
                    output=True)

    # play. May repeat with different volume values (if done interactively) 
    stream.write(volume*samples)
    stream.stop_stream()
    stream.close()
    p.terminate()




f = 440.0        # sine frequency, Hz, may be float    
duration = 10.0   # in seconds, may be float
fs = 44100       # sampling rate, Hz, must be integer
# generate samples, note conversion to float32 array
# for paFloat32 sample values must be in range [-1.0, 1.0]
samples = (np.sin(2*np.pi*np.arange(fs*duration)*f/fs)).astype(np.float32)

play_shit(samples, fs=44100, volume=0.5)