#!/Users/fmriadmin/anaconda2/bin/python
"""

~~ Script for the CONV study ~~

TASK:

Participants, in pairs, are simultaneously scanned while they take turns talking about a public health topic for 10 minutes.
The conversation is preceded and followed by a video.

Timing of each run (with TR=1500ms)
video_air1: 405s = 270 TRs
video_air2: 412s = 277 TRs

PRIOR TO RUNNING THE SCRIPT:

System clocks on the control computers (or laptops running the script) need to be synced prior to running the experiment.
This can be done by either using the same NTP servers or by syncing to the same GPS time signal.

WHAT THE SCRIPT DOES:

(1) Synchronizes task start on both control computers by having the two computers exchange timestamps and deriving
    a common start time from those timestamps. Syncing start time across computers is done using a third UDP socket pair.

(2) Transmit audio between distant sites using UDP. UDP hole punching is used to establish UDP connections with systems behind NAT.
    UDP packets are uncompressed audio data appended with timestamps.

(3) Uses PsychoPy for visual stimuli and mouse events.

NOTES:

- 4 arguments are passed to the script: dyad #, subj letter (a or b), condition (1 or 0), and video order (1 or 0)
- This script has only been tested on OS X. 10.12.
- This script uses PsychoPy 3; have not tested using PsychoPy versions with python 2.7.
- TTL pulse keypress at Princeton is the equal sign (=).
- Hardcoded variables can be found in the magicNumbers function. If you need to set parameters, that's where you should go.
- This script is capable of simple NAT traversal. If NATs are present and there are two firewalls, a static IP address is required at least on one end.
- With UDP, we may lose a few packages, but it shouldn't affect audio quality much.
    To account for fluctuations in travel time / losses, the program uses a simple two-ended continuous audio buffer, with only occasional
    health reports (nothing adaptive). You can control audio chunk and buffer size with command-line options --CHUNK and --BUFFER.
- We can check current NAT properties with stunclient using the input option --STUN.
- If code doesn't work b/c it can't figure out which version of Python to use,
    specify where python is at the top of the code:
        for home: #!/anaconda3/python.app/Contents/MacOS/python
        for scanner: #!/Users/fmriadmin/anaconda2/bin/python
        for home testing, add this after importing sys:
            sys.path.append('/anaconda3/lib/python3.6/site-packages')

CREDITS:
Audio link and task synchronization was made possible with the help of Adam Boncz.

Author: Lily Tsoi
Edited for new study: Shannon Burns
Last modified: September 1, 2021

"""

## -- import modules and provide python 2.7 / 3 compatibility --
from __future__ import annotations

import os
import sys

# for home testing
sys.path.append("/anaconda3/lib/python3.6/site-packages")

import argparse
import builtins
import csv
import datetime
import multiprocessing
import re
import socket
import struct
import subprocess
import time
from operator import sub

import pyaudio

# Python 2.7 / 3 compatibility
# get python 3 flush arg behavior on 2.7 for print function
if sys.version_info[:2] < (3, 3):
    old_print = print

    def print(*args, **kwargs):
        flush = kwargs.pop("flush", False)
        old_print(*args, **kwargs)
        file = kwargs.get("file", sys.stdout)
        if flush and file is not None:
            file.flush()


# get python 3 user input behavior
if hasattr(__builtins__, "raw_input"):
    input = raw_input

## -- set default directory --

# Ensure that relative paths start from the same directory as this script
_thisDir = os.path.dirname(os.path.abspath(__file__))
os.chdir(_thisDir)

## -- enter hardcoded values here --


def magicNumbers():
    ## -- set role --

    # first digit of PARTICIPANT ID (0 and 1 for real participants; 8 and 9 for pilot testing)
    if (SUBJ == 1) | (SUBJ == 8):  # Skyra, need IP for Prisma
        role = "A"
        scanner = "skyra"
        # IP = '10.8.188.205'
        IP = "101.0.0.2"
    elif (SUBJ == 2) | (SUBJ == 9):  # Prisma, need IP for Skyra
        role = "B"
        scanner = "prisma"
        # IP = '10.9.46.59'
        IP = "101.0.0.1"
    else:
        print("\nSubject ID was not entered correctly. Closing the experiment now.")
        sys.exit()

    ## -- set audio and networking settings --

    # default audio settings: mono, 16kHz sampling, 16bit encoding
    CHANNELS = 1
    RATE = 16000
    FORMAT = pyaudio.paInt16

    # UDP hole punch timeout (time for handshake), in seconds
    punchTimeout = 90

    # default port numbers
    # local
    portIn = 30002
    portOut = 30001
    # remote
    PortIn = 30002
    PortOut = 30001
    PortComm = 30003

    ## -- stimuli settings --

    exp_name = "DIAD"
    win_x = 1920  # 1920 # window size in px; for scanner comp: 1920
    win_y = 1080  # 1080 # window size in px; for scanner comp: 1080
    win_fullscr = False
    start_lag = 12  # time variable for start sync (seconds)
    letter_h = 0.06
    wrap_w = 1.2
    intro_time = 3  # in seconds
    comm_time = 600  # in seconds (60 * 10)
    between_time = 3  # in seconds

    key_list = ["5", "escape"]  # '=' for TTL

    ## -- set default filenames --
    datet = datetime.datetime.now()
    date_str = datet.strftime("%Y%m%d_%H%M%S")
    dyad_str = str(DYAD)
    subj_str = str(SUBJ)
    savefileLog = (
        _thisDir
        + os.sep
        + "data/%s_%s-%s_TimingsLog_%s.csv" % (exp_name, dyad_str, subj_str, date_str)
    )
    savefileTTL = (
        _thisDir
        + os.sep
        + "/data/%s_%s-%s_TTLtimestamps_%s.csv"
        % (exp_name, dyad_str, subj_str, date_str)
    )
    savefileTimestamps = (
        _thisDir
        + os.sep
        + "data/%s_%s-%s_timestamps_%s.csv" % (exp_name, str(DYAD), str(SUBJ), date_str)
    )

    return (
        punchTimeout,
        CHANNELS,
        RATE,
        FORMAT,
        date_str,
        savefileLog,
        savefileTTL,
        savefileTimestamps,
        portIn,
        portOut,
        PortIn,
        PortOut,
        PortComm,
        role,
        IP,
        scanner,
        exp_name,
        win_x,
        win_y,
        win_fullscr,
        start_lag,
        letter_h,
        wrap_w,
        intro_time,
        comm_time,
        between_time,
        key_list,
    )


## STUN query function
# This is called if you want to explore NAT properties.
# This is a wrapper around the stunclient tool. When STUN arg is set to 1,
# we just call it in a subprocess and capture some of its output.
# This function is not needed for the script to work, but it is
# included here for NAT diagnostic purposes.


def stunQuery():
    # list of a few servers to try
    serverList = ["stun.ekiga.net", "stun.ideasip.com", "stun.stunprotocol.org"]

    # try servers until we get a successful test,
    # write output to terminal (NAT behavior type, internal and mapped
    # addressses, etc)
    stunFlag = False
    for i in serverList:
        print("Trying stun server ", i, "...")
        try:
            stunOutput = subprocess.check_output(
                ["stunclient", "--mode", "full", i], universal_newlines=True
            )
            if bool(re.search("Behavior test: success", stunOutput)):
                for lines in stunOutput.splitlines():
                    print(lines)
                stunFlag = True
                break
        except:
            print("Stun query failed...")
    # if all queries fail, we have a problem
    if not stunFlag:
        print("All stun queries failed, no successful NAT behavior test")
    return stunFlag


## Socket function
# Opens simple UDP socket, binds it to given port number at localhost.


def openSocket(port):
    socketFlag = False
    # define socket
    try:
        socketUDP = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        print("\nSocket created")
    except OSError:
        print("\nFailed to create UDP socket")
    # bind port
    host = ""  # localhost
    try:
        socketUDP.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        socketUDP.bind((host, port))
        print("\nUDP socket bound to local port: ", port)
        socketFlag = True
    except OSError:
        print("\nFailed to bind UDP socket to local port ", port)
    return socketFlag, socketUDP


## Hole punch function
# Here, we assume that there is one side behind NAT. That side
# needs to initiate UDP hole punching (NAT traversal). This function
# handles UDP hole punching in eiher case, for both in- and outgoing
# communication


def punchThrough(NAT, socketIn, socketOut, socketComm, punchTimeout):
    global IP, PortIn, PortOut, PortComm
    # if other side is behind NAT, script just waits for connection,
    # both for socketIn and socketOut
    if NAT == 0:
        print("\n\nWaiting for other end to initiate handshake...\n")
        start = time.time()
        recFlag = False
        # for this part, socket is non-blocking with a timeout
        socketIn.settimeout(1)
        socketOut.settimeout(1)
        socketComm.settimeout(1)
        # until there is incoming message or max time is up
        while (not recFlag) & (abs(time.time() - start) < punchTimeout):
            try:
                incomingIn, addressIn = socketIn.recvfrom(1024)
                incomingOut, addressOut = socketOut.recvfrom(1024)
                incomingComm, addressComm = socketComm.recvfrom(1024)
                # if we have incoming message 'hello!'
                if (
                    bool(incomingIn == b"hello!")
                    & bool(incomingOut == b"hello!")
                    & bool(incomingComm == b"hello!")
                    & bool(addressIn[0] == addressOut[0])
                ):
                    print(
                        "\nHandshake initiated from ",
                        addressIn[0],
                        ":",
                        addressIn[1],
                        " and :",
                        addressOut[1],
                    )
                    IP = addressIn[0]

                    # NEW PART, SETTING REMOTE PORTS ACCORDING TO
                    # ADDRESS OF INCOMING PACKETS
                    PortOut = addressIn[1]
                    PortIn = addressOut[1]
                    PortComm = addressComm[1]
                    print("\nRemote ports are", PortIn, PortOut, PortComm, "\n")

                    recFlag = True
            except:
                print("No handshake message yet...", end="\r", flush=True)
        # if time is over
        if not recFlag:
            IP = []
            return recFlag

        # send answer, wait until handshake is confirmed
        print("\nResponding...\n")
        recFlag = False
        start = time.time()
        # send answer and listen to next message, until time is up
        while (not recFlag) & (abs(time.time() - start) < punchTimeout):
            try:
                socketIn.sendto(b"hello!", (IP, PortOut))
                socketOut.sendto(b"hello!", (IP, PortIn))
                socketComm.sendto(b"hello!", (IP, PortComm))
            except:
                print('\n\nCould not send "hello" packet to ', IP)
            # see if there was answer
            try:
                incomingIn, addressIn = socketIn.recvfrom(1024)
                incomingOut, addressOut = socketOut.recvfrom(1024)
                incomingComm, addressComm = socketComm.recvfrom(1024)
                if (
                    bool(incomingIn == b"hello!")
                    & bool(addressIn[0] == IP)
                    & bool(incomingOut == b"hello!")
                    & bool(addressOut[0] == IP)
                    & bool(incomingComm == b"hello!")
                    & bool(addressComm[0] == IP)
                ):
                    print("\nHandshake confirmed, other end is ready\n")
                    recFlag = True
            except:
                print("No confirmation yet", end="\r", flush=True)
        # if there was no answer in the maximum allowed time
        if not recFlag:
            IP = []

    # if other end is behind NAT, it initiates hole punching. We assume
    # current side to be reachable via public IP and given port
    if NAT == 1:
        # actual handshake part
        print("\n\nInitiating handshake...\n")
        start = time.time()
        recFlag = False
        # for this part, socket is non-blocking with a timeout
        socketIn.settimeout(1)
        socketOut.settimeout(1)
        socketComm.settimeout(1)
        # send handshake and listen for answer until time is up
        # when sending, make sure to 'cross' the in and out ports between
        # local and remote
        print('\nSending handshake message "hi partner"...\n')
        while (not recFlag) & (abs(time.time() - start) < punchTimeout):
            try:
                socketIn.sendto(b"hi partner", (IP, PortOut))
                socketOut.sendto(b"hi partner", (IP, PortIn))
                socketComm.sendto(b"hi partner", (IP, PortComm))
            except:
                print(
                    '\n\nCould not send "hi partner" packet to ',
                    IP,
                )
            # see if there was an answer
            try:
                incomingIn, addressIn = socketIn.recvfrom(1024)
                incomingOut, addressOut = socketOut.recvfrom(1024)
                incomingComm, addressComm = socketComm.recvfrom(1024)
                if (
                    bool(incomingIn == b"hi partner")
                    & bool(addressIn[0] == IP)
                    & bool(incomingOut == b"hi partner")
                    & bool(addressOut[0] == IP)
                    & bool(incomingComm == b"hi partner")
                    & bool(addressComm[0] == IP)
                ):
                    print("\nReceived answer, handshake confirmed\n")
                recFlag = True
            except:
                print("No proper answer yet", end="\r", flush=True)

        # if time is over
        if not recFlag:
            return recFlag

        # if handshake was successful, send a signal asking for audio
        if recFlag:
            # repeat final message a few times
            # 'cross' in and out ports across machines again
            for i in range(5):
                socketIn.sendto(b"please", (IP, PortOut))
                socketOut.sendto(b"please", (IP, PortIn))
                socketComm.sendto(b"please", (IP, PortComm))
            print("\nUDP hole punched, we are happy and shiny\n")

    # flush the sockets before we go on
    start = time.time()
    while abs(start - time.time()) < 1:
        try:
            incomingIn = socketIn.recv(1024)
            incomingOut = socketOut.recv(1024)
            incomingComm = socketComm.recv(1024)
        except:
            pass
    return recFlag


## Callback function for non-blocking pyaudio (portaudio) input
# Important!! We put everything that is to be done with the data into
# the callback function. Specifically, callback saves input audio,
# handles timestamps and counters and sends UDP packets.

# Expects output file to be open and ready for writing.
# Expects UDP socket and connection to server to be ready.
# Expects packetCounter to be set up.


def callbackInput(in_data, frame_count, time_info, status):
    # keep track of chunks
    global chunkCounter
    # refresh counter
    chunkCounter += 1
    # following line changed for python 2.7
    bytepacket = struct.pack("<l", chunkCounter)
    # write out new data before we mess with it
    fOut.write(in_data)
    # create bytearray from the audio chunk, so we can expand it
    dataArray = bytearray(in_data)
    # append data with timestamp and packetCounter
    timestamp = time.time()
    bytestamp = struct.pack("<d", timestamp)  # convert float into bytes
    # extend dataArray, get final packet to send
    dataArray.extend(bytepacket)
    dataArray.extend(bytestamp)
    in_data = bytes(dataArray)
    # send new data to other side
    try:
        socketOut.sendto(in_data, (IP, PortIn))
    except OSError:
        print("Failed to send packet, chunkCounter = " + str(chunkCounter))
    # return data and flag
    return (in_data, pyaudio.paContinue)


## Callback function for non-blocking pyaudio (portaudio) output.


# In this version, we use a simple continuous buffer that collects
# all incoming packets on one end and is read out by the callback on the other.
# Important!!
# Expects output files to be open and ready for writing.
# Expects UDP socket and connection to server to be ready.
# Expects all four counters + changeFlag to be set up.
def callbackOutput(in_data, frame_count, time_info, status):
    global lastData, underFlowFlag
    # once the buffer is filled for the first time, startFlag is set and
    # callback can read from it
    if startFlag:
        # first check if there is enough data available to read
        if len(audioBuffer) > CHUNK * 2:
            data = audioBuffer[0 : CHUNK * 2]
            del audioBuffer[0 : CHUNK * 2]
            lastData = data
        # if buffer is empty, update the underflow counter
        else:
            data = lastData
            underFlowFlag += 1

    # until startFlag is set, callback reads from a silence buffer (zeros)
    elif len(silenceBuffer) > CHUNK * 2:
        data = silenceBuffer[0 : CHUNK * 2]
        del silenceBuffer[0 : CHUNK * 2]
        lastData = data
    else:
        data = lastData

    data = bytes(data)
    fOut.write(data)
    return data, pyaudio.paContinue


## Function to set up mic
# Uses pyaudio (portaudio) for a non-blocking input device.
# Device is default input set on platform.


def micOpen(FORMAT, CHANNELS, RATE, CHUNK):
    p = pyaudio.PyAudio()
    stream = p.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=RATE,
        input=True,
        frames_per_buffer=CHUNK,
        stream_callback=callbackInput,
        start=False,
    )  # IMPORTANT: don't start yet
    return stream, p


## Function to open output device
# Uses pyaudio (portaudio). Chooses default output device on platform.


def speakersOpen(FORMAT, CHANNELS, RATE, CHUNK):
    # open pyaudio (portaudio) device
    p = pyaudio.PyAudio()
    # open portaudio output stream
    stream = p.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=RATE,
        output=True,
        frames_per_buffer=CHUNK,
        stream_callback=callbackOutput,
        start=False,
    )  # IMPORTANT: don't start yet
    return stream, p


## Function to strip packetCounter and client timestamp from UDP packets
# the last 8 bytes is the timestamp, the 4 before that is the packetNumber
def packetParser(dataIn):
    dataArray = bytearray(dataIn)
    audio = dataArray[0 : len(dataIn) - 12]
    # using struct unpack to stay compatible with 2.7
    packetNumber = struct.unpack("<l", dataArray[len(dataIn) - 12 : len(dataIn) - 8])
    packetNumber = packetNumber[0]
    timePacket = struct.unpack("<d", dataArray[len(dataIn) - 8 : len(dataIn)])
    return audio, packetNumber, timePacket


## Cleanup function for input (microphone)
# Close and terminate pyaudio, close socket, close files.
def cleanupInput():
    print("\nTransmission finished, cleaning up input...\n")
    # end signal in UDP packet
    for i in range(5):
        try:
            closePacket = b"thanks"
            socketOut.sendto(closePacket, (IP, PortIn))
            print("Sending closing packets", end="\r", flush=True)
        except:
            print("Sending closing packet failed")
    print("\nClosing portaudio input device, sockets, files\n")
    # pyaudio
    streamInput.stop_stream()
    streamInput.close()
    pIn.terminate()
    # sockets
    socketOut.close()
    socketComm.close()
    # files
    fOut.close()


## Cleanup function for output (speakers)
# Close and terminate pyaudio, close socket, close files.
def cleanupOutput():
    print("\nTransmission finished, cleaning up output...\n")
    print("\nClosing portaudio output device, sockets, files\n")
    # pyaudio
    streamOutput.stop_stream()
    streamOutput.close()
    pOut.terminate()
    # sockets
    socketIn.close()


## Cleanup on keypress ESC
def cleanup(queueInput, queueOutput, audioInput, audioOutput, fLog, fTTL):
    print("\nTerminating...\n")
    # killing audio I/O
    queueInput.put("die")
    queueOutput.put("die")
    audioInput.join()
    audioOutput.join()

    # close log files
    fLog.close()
    fTTL.close()

    sys.exit()


## Function to open all needed sockets and handle NAT


def networkInit(STUN, NAT, portIn, portOut, punchTimeout):
    global socketIn, socketOut, socketComm

    # if STUN was asked
    if STUN:
        stunFlag = stunQuery()
        if not stunFlag:
            print(
                "\n\nSTUN query failed, something is wrong. Check "
                + "connection. Do you have stuntman installed?"
            )
    # UPD sockets for transmission
    socketFlag, socketOut = openSocket(portOut)
    if not socketFlag:
        print("\n\nCould not create or bind UDP socket. Uh-oh.")
        sys.exit()
    socketFlag, socketIn = openSocket(portIn)
    if not socketFlag:
        print("\n\nCould not create or bind UDP socket. Uh-oh.")
        sys.exit()
    socketFlag, socketComm = openSocket(PortComm)
    if not socketFlag:
        print("\n\nCould not create or bind UDP socket. Uh-oh.")
        sys.exit()
    # Hole punch
    recFlag = punchThrough(NAT, socketIn, socketOut, socketComm, punchTimeout)
    if not recFlag:
        print("\n\nSomething went wrong at NAT traversal. Uh-oh.")
        sys.exit()
    # set sockets to non-blocking
    socketOut.settimeout(0)
    socketIn.settimeout(0.1)
    socketComm.settimeout(0.1)


## Main input (microphone) function. Handles audio input and
# transmission. Should be called in separate process (multiprocessing()),
# after networkInit(), at the same time as outputProcess()
def inputProcess(FORMAT, CHANNELS, RATE, CHUNK, queueInput):
    global chunkCounter, streamInput, pIn
    # init chunkCounter
    chunkCounter = 0
    # open input dev
    streamInput, pIn = micOpen(FORMAT, CHANNELS, RATE, CHUNK)

    # print start message
    print("\nEverything seems all right, channel open on our side.\n")

    while True:
        note = queueInput.get()
        if note == "stop":
            streamInput.stop_stream()
        elif note == "start":
            # start input stream
            start = time.time()
            streamInput.start_stream()
        elif note == "die":
            break
        # wait until all audio is sent
        while streamInput.is_active():
            time.sleep(0.01)
            # if escape key was pressed, terminate
            if not queueInput.empty():
                break

    # input cleanup
    cleanupInput()


## Main output (receiver) function. Handles audio output and
# packet control. Should be called in separate process (multiprocessing()),
# after networkInit(), at the same time as inputProcess()
def outputProcess(
    BUFFER, CHUNK, FORMAT, CHANNELS, RATE, queueOutput, savefileTimestamps
):
    # these need to be global...
    global underFlowFlag, startFlag, audioBuffer, streamOutput
    global pOut, silenceBuffer, lastData

    # initialize buffer underflow / overflow flags, callback start flag
    underFlowFlag = 0
    startFlag = 0
    overFlowFlag = 0

    # Lists to store incoming packet numbers, client side timestamps and
    # server side timestamps
    packetListClient = list()
    packetListClient.append(0)
    timeListClient = list()
    timeListServer = list()

    # open output dev
    streamOutput, pOut = speakersOpen(FORMAT, CHANNELS, RATE, CHUNK)
    print("\nAudio output set up, waiting for transmission.")

    # counter for all received UDP packets
    packetCounter = 0

    # create buffer for incoming packets
    audioBuffer = bytearray()

    # start stream with a silent buffer (silence buffer)
    silenceBuffer = b"x\00" * 2 * CHUNK * BUFFER
    silenceBuffer = bytearray(silenceBuffer)
    lastData = silenceBuffer[0 : CHUNK * 2]

    while True:
        note = queueOutput.get()
        if note == "stop":
            streamOutput.stop_stream()
        elif note == "start":
            streamOutput.start_stream()
        elif note == "die":
            break

        # wait until all audio is sent
        while streamOutput.is_active():
            if not queueOutput.empty():
                break

            # receive UDP packet - remember this is in non-blocking mode now!
            packet = []
            try:
                packet = socketIn.recv(CHUNK * 4)
            except:
                pass
            # if we received anything
            if packet:
                # other end can end session by sending a specific message
                # ('thanks')
                if packet == b"thanks":
                    print("thanks (end message) received, finishing output")
                    break
                # parse packet into data and the rest
                data, packetNumber, timePacket = packetParser(packet)
                # adjust packet counter
                packetCounter += 1

                # do a swap if packetNumber is smaller than last
                if packetNumber > 3:
                    if packetNumber < packetListClient[-1]:
                        try:  # buffer could be empty...
                            audioBuffer.extend(audioBuffer[-CHUNK * 2 :])
                            audioBuffer[-CHUNK * 4 : -CHUNK * 2] = data
                        except:
                            audioBuffer.extend(data)
                    else:
                        # otherwise just append audioBuffer with new data
                        audioBuffer.extend(data)
                else:
                    # otherwise just append audioBuffer with new data
                    audioBuffer.extend(data)

                # get server-side timestamp right after writing data to buffer
                timeListServer.append(time.time())

                # set startFlag for callback once buffer is filled for the first
                # time
                if packetCounter == BUFFER:
                    startFlag = 1

                # if audioBuffer is getting way too long, chop it back, the
                # threshold is two times the normal size
                if len(audioBuffer) > 2 * CHUNK * BUFFER * 2:
                    del audioBuffer[0 : 2 * CHUNK * BUFFER]
                    overFlowFlag += 1

                # append timePacket and packetNumber lists
                packetListClient.append(packetNumber)
                timeListClient.append(float(timePacket[0]))

        if note == "run_end":
            # end messages
            messagesOutput(
                packetCounter,
                timeListServer,
                timeListClient,
                packetListClient,
                overFlowFlag,
                savefileTimestamps,
            )

    # cleanup
    cleanupOutput()


# %% Function to display closing stats and messages for output
def messagesOutput(
    packetCounter,
    timeListServer,
    timeListClient,
    packetListClient,
    overFlowFlag,
    savefileTimestamps,
):
    try:
        # summary message
        print(
            "\n\n"
            + "Time taken for all chunks: "
            + str(timeListServer[-1] - timeListServer[0])
            + " = "
            + str((timeListServer[-1] - timeListServer[0]) / 60)
            + " minutes"
        )
        # more diagnostic messages
        print("\nReceived " + str(packetCounter) + " audio chunks")
        # underflow events
        print("\nBuffer underflow occurred " + str(underFlowFlag) + " times")
        # overflow events
        print("\nBuffer overflow occurred " + str(overFlowFlag) + " times")
        # print average transmission time
        timeListDiff = list(map(sub, timeListServer, timeListClient))
        print(
            "\nAverage difference between client and server side timestamps: ",
            sum(timeListDiff) / len(timeListDiff),
            " secs \n\nClient timestamp "
            "is taken after reading audio input buffer \nServer timestamp is "
            "taken when pushing the received data into audio output buffer\n\n",
        )
    except:
        print("\nCould not provide summary message")

    try:
        # Saving data
        # write out timestamps into a csv file
        output = builtins.open(savefileTimestamps, "a+")
        writer = csv.writer(output)
        writer.writerow(("packet", "client", "server", "diff"))
        for packet, client, server, diff in zip(
            packetListClient, timeListClient, timeListServer, timeListDiff
        ):
            writer.writerow((packet, client, server, diff))
        output.close()
        return
    except:
        print("\nCould not save data")


## Function to integrate the pieces and run the whole thing
def goGo(NAT, STUN, LOGTTL, DYAD, SUBJ, COND):
    ## -- take care of network, audio, and TTL recording --

    # these need to be global for callbacks, etc.
    global IP, fOut, PortIn, PortOut, PortComm, run_n

    # load all settings, magic numbers
    [
        punchTimeout,
        CHANNELS,
        RATE,
        FORMAT,
        date_str,
        savefileLog,
        savefileTTL,
        savefileTimestamps,
        portIn,
        portOut,
        PortIn,
        PortOut,
        PortComm,
        role,
        IP,
        scanner,
        exp_name,
        win_x,
        win_y,
        win_fullscr,
        start_lag,
        letter_h,
        wrap_w,
        intro_time,
        comm_time,
        between_time,
        key_list,
    ] = magicNumbers()

    # networkInit
    networkInit(STUN, NAT, portIn, portOut, punchTimeout)

    # set filenames
    savefileOut = (
        _thisDir
        + os.sep
        + "data/DIAD_"
        + str(DYAD)
        + "-"
        + str(SUBJ)
        + "_RecordedAudio_"
        + date_str
    )

    # open files we will use for writing stuff out
    fOut = builtins.open(savefileOut, "wb")  # audio file
    fLog = builtins.open(savefileLog, "w")  # text file
    fTTL = builtins.open(savefileTTL, "w")  # text file

    # write headers for text files
    fLog.write("condition,role,time.time,run.time,comm.time,audio_position\n")
    fTTL.write("condition,role,segment,time.time,run.time,comm.time\n")

    # audio I/O processes and TTL recording run in separate processes
    queueInput = multiprocessing.Queue()
    queueOutput = multiprocessing.Queue()
    audioInput = multiprocessing.Process(
        name="audioInput",
        target=inputProcess,
        args=(
            FORMAT,
            CHANNELS,
            RATE,
            CHUNK,
            queueInput,
        ),
    )
    audioOutput = multiprocessing.Process(
        name="audioOutput",
        target=outputProcess,
        args=(
            BUFFER,
            CHUNK,
            FORMAT,
            CHANNELS,
            RATE,
            queueOutput,
            savefileTimestamps,
        ),
    )
    audioInput.start()
    audioOutput.start()

    ## -- check audio status --

    print("\naudio input alive code: " + str(audioInput.is_alive()))
    print("audio output alive code: " + str(audioOutput.is_alive()))
    if not audioInput.is_alive() or not audioOutput.is_alive():
        print(
            "\nAudio inputs and outputs are not alive. Terminating so you can start over again."
        )
        cleanup(queueInput, queueOutput, audioInput, audioOutput, fLog, fTTL)

    ## -- import the rest of PsychoPy --

    # If psychopy is imported before the multiprocesses start, the code won't work
    from psychopy import core, data, event, logging, monitors, visual
    from psychopy.hardware import keyboard

    ## -- set up save files --

    # Data file name stem = absolute path + name; later add .csv, .log, etc
    filename = _thisDir + os.sep + "data/%s_%s-%s_%s" % (exp_name, DYAD, SUBJ, date_str)

    # An ExperimentHandler isn't essential but helps with data saving
    thisExp = data.ExperimentHandler(
        name=exp_name,
        version="",
        extraInfo="",
        runtimeInfo=None,
        originPath="_thisDir" + os.sep + "DIAD_scan_air.py",
        savePickle=True,
        saveWideText=True,
        dataFileName=filename,
    )
    # save a log file for detail verbose info
    logFile = logging.LogFile(filename + ".log", level=logging.EXP)
    logging.console.setLevel(logging.WARNING)  # this outputs to the screen, not a file
    frameTolerance = 0.001
    endExpNow = False
    defaultKeyboard = keyboard.Keyboard()

    # assign first speaker
    first_speaker = ["A", "B"]

    ## -- start visual part of the experiment --

    # set up monitor info
    if scanner == "skyra":
        my_monitor = monitors.Monitor(name=scanner)
        my_monitor.setSizePix((1920, 1080))
        my_monitor.setWidth(64)
        my_monitor.setDistance(89)  # 89 after Jan
        my_monitor.save()
    elif scanner == "prisma":
        my_monitor = monitors.Monitor(name=scanner)
        my_monitor.setSizePix((1920, 1080))
        my_monitor.setWidth(56)
        my_monitor.setDistance(107.5)  # 107.5 after Jan
        my_monitor.save()

    try:
        win = visual.Window(
            size=[win_x, win_y],
            color="black",
            fullscr=win_fullscr,
            screen=1,
            monitor=my_monitor,
        )
        win.mouseVisible = True
    except:
        print("\nProblem while setting up window")
        cleanup(queueInput, queueOutput, audioInput, audioOutput, fLog, fTTL)

    ## -- initialize text stim --

    # text for roles and messages
    turn_speak_text = "YOUR TURN TO SPEAK"
    turn_listen_text = "YOUR TURN TO LISTEN"
    trial_turn_last = "[This is the end of your turn. Click the mouse to pass the mic.]"

    show_sync = visual.TextStim(
        win,
        name="show_sync",
        text="Syncing start time with your partner...",
        pos=(0, 0),
        height=letter_h,
        wrapWidth=wrap_w,
        color="white",
        autoLog=False,
    )
    show_instructions = visual.TextStim(
        win,
        name="show_instructions",
        text="",
        pos=(0, 0),
        height=letter_h,
        wrapWidth=wrap_w,
        color="white",
        autoLog=False,
    )
    show_prompt = visual.TextStim(
        win=win,
        name="show_prompt",
        text="",
        pos=(0, 0.2),
        height=letter_h,
        color="white",
        autoLog=False,
    )
    show_role = visual.TextStim(
        win=win,
        name="show_role",
        text="",
        pos=(0, 0.7),
        height=letter_h,
        wrapWidth=wrap_w,
        color="white",
        autoLog=False,
    )
    show_pass = visual.TextStim(
        win=win,
        name="show_pass",
        text="",
        pos=(0, 0),
        height=letter_h,
        color="white",
        autoLog=False,
    )
    show_lines = visual.TextStim(
        win=win,
        name="show_lines",
        text="",
        pos=(0, 0),
        height=letter_h,
        wrapWidth=wrap_w,
        color="white",
        autoLog=False,
    )
    show_lines_done = visual.TextStim(
        win=win,
        name="show_lines_done",
        text="Please wait until the timer is done.",
        pos=(0, 0),
        height=letter_h,
        wrapWidth=wrap_w,
        color="white",
        autoLog=False,
    )
    show_timer = visual.TextStim(
        win=win,
        name="show_timer",
        text="",
        pos=(0, -0.7),
        height=letter_h,
        color="white",
        autoLog=False,
    )
    show_blank = visual.TextStim(
        win,
        name="show_blank",
        text="+",
        pos=(0, 0),
        height=letter_h,
        wrapWidth=wrap_w,
        color="white",
        autoLog=False,
    )
    show_end = visual.TextStim(
        win=win,
        name="show_end",
        text="You are now done with the conversation.",
        pos=(0, 0),
        height=letter_h,
        wrapWidth=wrap_w,
        color="white",
        autoLog=False,
    )

    ## -- sync computers --

    # put up a sync screen while we are waiting for startTimeCommon
    show_sync.draw()
    win.flip()

    # Sync process: (1) handshake to start, (2) exchange time stamps,
    # derive common start time (max of time stamps + start_lag)
    commFlag = True
    incoming = []
    # first a handshake for sync
    print("\nStarting sync\n")
    while commFlag:
        # send packets
        try:
            socketComm.sendto(b"syncTimeNow", (IP, PortComm))
        except:
            print("\nProblem sending a syncTimeNow packet...\n")
        try:
            incoming = socketComm.recv(CHUNK)
        except:
            pass
        if incoming == b"syncTimeNow":
            incoming = []
            # time stamp on our side
            timeHere = time.time()
            print("\nReceived sync handshake, sending timeHere", str(timeHere), "\n")
            while True:
                keys = event.getKeys(key_list)
                if keys:
                    if keys[0] == "escape":
                        cleanup(
                            queueInput, queueOutput, audioInput, audioOutput, fLog, fTTL
                        )
                        return

                # send our time stamp
                for i in range(2):
                    try:
                        socketComm.sendto(struct.pack("<d", timeHere), (IP, PortComm))
                    except:
                        print("\nProblem sending a timeHere packet...\n")
                # read out socket
                try:
                    incoming = socketComm.recv(CHUNK)
                except:
                    pass
                # if readout data is what we would expect, create startTime
                if bool(incoming) & bool(len(incoming) == 8):
                    print("\nGot incoming time\n")
                    # unpack time stamp from other side
                    timeThere = struct.unpack("<d", incoming)[0]
                    print("\nIncoming timeThere is", str(timeThere), "\n")
                    # start is at the max of the two timestamps + a predefined lag
                    startTimeCommon = max(timeThere, timeHere) + start_lag
                    print("\nGot shared startTimeCommon:", str(startTimeCommon), "\n")
                    commFlag = False
                    # insurance policy - send it last time
                    for i in range(2):
                        socketComm.sendto(struct.pack("<d", timeHere), (IP, PortComm))
                    break

    # common start is synced at a precision of
    # keyboard polling (few ms) + ntp diff + hardware jitter
    while time.time() < startTimeCommon:
        keys = event.getKeys(key_list)
        if keys:
            if keys[0] == "escape":
                cleanup(queueInput, queueOutput, audioInput, audioOutput, fLog, fTTL)
                return

    ## -- initialize mouse --
    mouse = event.Mouse(win=win)
    mouse_pos_x, mouse_pos_y = [None, None]

    ## -- initialize clocks --
    global_clock = core.Clock()
    conv_clock = core.Clock()
    blank_clock = core.Clock()
    intro_clock = core.Clock()
    comm_clock = core.Clock()
    mouse.mouseClock = core.Clock()

    # mute the mic and speaker
    queueInput.put("stop")
    queueOutput.put("stop")

    ## -- set up beginning of run instructions --

    persuade_instr_text = (
        "Next, you will discuss with the other participant how the charity money "
        "should be allocated."
        "\n\nIMPORTANT: During this conversation, try to PERSUADE "
        "the other person of your opinion. "
        "\n\nWe are studying how persuasion works in the brain, so please try to "
        "convince the other person of your opinion as much as possible and get them "
        "to understand your perspective."
        "\n\nThese instructions are only for you, so please don't share them "
        "with the other participant."
        "\n\nYou will have 10 minutes for this conversation. "
        "A timer will show you how many seconds are left. "
        "\n\nTell the experimenter when you are ready to begin."
    )

    compromise_instr_text = (
        "Next, you will discuss with the other participant how the charity money "
        "should be allocated."
        "\n\nIMPORTANT: During this conversation, try to find a "
        "JOINT SOLUTION that you both agree on. "
        "\n\nWe are studying how collaboration works in the brain, so please try to "
        "reconcile any differences of opinion as much as possible and look for "
        "a shared perspective."
        "\n\nThese instructions are only for you, so please don't share them "
        "with the other participant."
        "\n\nYou will have 10 minutes for this conversation. "
        "A timer will show you how many seconds are left. "
        "\n\nTell the experimenter when you are ready to begin."
    )

    if COND == 0:
        conv_instr_text = persuade_instr_text
    else:
        conv_instr_text = compromise_instr_text

    # -- Conversation --

    # draw, flip
    keys = event.getKeys()
    show_instructions.setText(conv_instr_text)
    show_instructions.draw()
    win.flip()

    # wait for trigger to start task
    triggered = False
    while not triggered:
        keys = event.getKeys(key_list)
        if keys:
            print("keys pressed: " + str(keys))
            # if event.getKeys returns a '=', its a TTL
            if keys[0] == "5":
                triggered = True
                fttl_time = time.time()
            # escape quits
            elif keys[0] == "escape":
                cleanup(queueInput, queueOutput, audioInput, audioOutput, fLog, fTTL)
                return

    # reset conv_clock
    conv_clock.reset()

    ## -- start logs --

    # hdr: fTTL.write('condition,role,segment,time.time,run.time,comm.time\n')
    fTTL.write(",,," + str(fttl_time) + "," + str(conv_clock.getTime()) + ",\n")
    # log audio file object positions at start
    # hdr: fLog.write('condition,role,time.time,run.time,comm.time,audio_position\n')
    fLog.write(
        ",,"
        + str(startTimeCommon)
        + ","
        + str(conv_clock.getTime())
        + ",,"
        + str(fOut.tell())
        + "\n"
    )

    ## -- blank screen before start --
    show_blank.draw()
    win.flip()
    blank_clock.reset()

    while blank_clock.getTime() < between_time:
        keys = event.getKeys(key_list)
        if keys:
            # if event.getKeys returns a '=', its a TTL
            if keys[0] == "5":
                # hdr: fTTL.write('condition,role,segment,time.time,comm.time,run.time\n')
                fTTL.write(
                    ",,blank,"
                    + str(time.time())
                    + ","
                    + str(conv_clock.getTime())
                    + ",\n"
                )
            # escape quits
            elif keys[0] == "escape":
                cleanup(queueInput, queueOutput, audioInput, audioOutput, fLog, fTTL)
                return

    ## -- set up texts displayed to participants --
    prompt_text = "How much of the 100 million should go to each option?"

    # if participant is the speaker
    if ((first_speaker[0] == "A") & (role == "A")) or (
        (first_speaker[0] == "B") & (role == "B")
    ):
        turn_role = "speaker"
        turn_role_text = turn_speak_text
        turn_pass_mic_text = "When you want to pass the mic, click your mouse"
    else:
        turn_role = "listener"
        turn_role_text = turn_listen_text
        turn_pass_mic_text = ""

    # update text for beginning of trial
    show_prompt.setText(prompt_text)
    show_role.setText(turn_role_text)
    show_pass.setText(turn_pass_mic_text)

    ## -- display trial: intro --

    show_prompt.draw()
    win.flip()
    intro_clock.reset()

    # log audio file object position for each trial
    # hdr: fLog.write('condition,role,time.time,run.time,comm.time,audio_position\n')
    fLog.write(
        str(COND)
        + ",turn_intro,"
        + str(time.time())
        + ","
        + str(conv_clock.getTime())
        + ",,"
        + str(fOut.tell())
        + "\n"
    )

    while intro_clock.getTime() < intro_time:
        keys = event.getKeys(key_list)
        if keys:
            # if event.getKeys returns a '=', its a TTL
            if keys[0] == "5":
                # hdr: fTTL.write('condition,role,segment,time.time,run.time,comm.time\n')
                fTTL.write(
                    str(COND)
                    + ",,"
                    + "intro"
                    + ","
                    + str(time.time())
                    + ","
                    + str(conv_clock.getTime())
                    + ",\n"
                )
            # escape quits
            elif keys[0] == "escape":
                cleanup(queueInput, queueOutput, audioInput, audioOutput, fLog, fTTL)
                return

    ## -- display trial: communication --

    # update text attributes
    show_role.setAutoDraw(True)
    show_timer.setAutoDraw(True)
    show_pass.setAutoDraw(True)
    show_lines.setAutoDraw(False)

    # update clock-related stuff
    turn_key = event.BuilderKeyResponse()
    turn_rt_time = []
    turn_rt_run = []
    turn_rt_comm = []

    # if speaker, start input stream and stop output stream, if listener, vice versa
    if turn_role == "speaker":
        queueInput.put("start")
        queueOutput.put("stop")
    elif turn_role == "listener":
        queueInput.put("stop")
        queueOutput.put("start")

    turn_onset = conv_clock.getTime()
    mouse.clickReset()
    comm_clock.reset()

    # log audio file pointer positions at turn changes
    # hdr: fLog.write('condition,role,time.time,run.time,comm.time,audio_position\n')
    fLog.write(
        str(COND)
        + ","
        + turn_role
        + ","
        + str(time.time())
        + ","
        + str(conv_clock.getTime())
        + ","
        + str(comm_clock.getTime())
        + ","
        + str(fOut.tell())
        + "\n"
    )

    while comm_clock.getTime() < comm_time:
        incoming = []
        keys = event.getKeys(key_list)
        mouseclick = mouse.getPressed()
        if sum(mouseclick) > 0:
            # if this participant is the speaker and he/she passes the mic - switch roles
            # if participant is the listener and the speaker presses key,
            # it is now the participant's turn to speak
            if turn_role == "speaker":
                speaker = role
                timeHere = time.time()
                runHere = conv_clock.getTime()
                commHere = comm_clock.getTime()
                turn_rt_time.append(timeHere)
                turn_rt_run.append(runHere)
                turn_rt_comm.append(commHere)
                turn_role_text = turn_listen_text
                turn_speak_text_turn = ""
                turn_role = "listener"
                turn_pass_mic_text = ""
                show_role.setText(turn_role_text, log=True)
                show_pass.setText(turn_pass_mic_text, log=False)
                # log audio file pointer positions at turn changes
                # hdr: fLog.write('condition,role,time.time,run.time,comm.time,audio_position\n')
                fLog.write(
                    str(COND)
                    + ","
                    + turn_role
                    + ","
                    + str(timeHere)
                    + ","
                    + str(runHere)
                    + ","
                    + str(commHere)
                    + ","
                    + str(fOut.tell())
                    + "\n"
                )
                # switch audio input and output attributes
                queueInput.put("stop")
                queueOutput.put("start")
                # transmit turn info to other computer
                # send packets
                try:
                    timestamps_to_send = struct.pack(
                        "<ddd", timeHere, runHere, commHere
                    )
                    socketComm.sendto(timestamps_to_send, (IP, PortComm))
                    print(
                        "\nTime when the mouseclick was sent out: "
                        + str(datetime.datetime.fromtimestamp(time.time()))
                    )
                except:
                    print("\nProblem sending a mouseclick packet...\n")
            mouse.clickReset()

        # if event.getKeys returns a '=', its a TTL
        if keys:
            if keys[0] == "5":
                # hdr: fTTL.write('condition,role,segment,time.time,run.time,comm.time\n')
                fTTL.write(
                    str(COND)
                    + ","
                    + str(turn_role)
                    + ","
                    + "communication"
                    + ","
                    + str(time.time())
                    + ","
                    + str(conv_clock.getTime())
                    + ","
                    + str(comm_clock.getTime())
                    + "\n"
                )

            # escape quits
            elif keys[0] == "escape":
                # transmit turn info to other computer
                # send packets
                try:
                    socketComm.sendto(b"esc", (IP, PortComm))
                    print(
                        "\nTime when the mouseclick was sent out: "
                        + str(datetime.datetime.fromtimestamp(time.time()))
                    )
                    cleanup(
                        queueInput, queueOutput, audioInput, audioOutput, fLog, fTTL
                    )
                    return
                except:
                    print("\nProblem sending escape packet...\n")

        # if participant is a listener
        if turn_role == "listener":
            speaker = list(set(["A", "B"]) - set([role]))[0]
            try:
                incoming = socketComm.recv(CHUNK)
            except:
                pass
            if bool(incoming) and bool(len(incoming) == 24):
                timeThere = struct.unpack("<ddd", incoming)[0]
                runThere = struct.unpack("<ddd", incoming)[1]
                commThere = struct.unpack("<ddd", incoming)[2]
                turn_rt_time.append(timeThere)
                turn_rt_run.append(runThere)
                turn_rt_comm.append(commThere)
                print(
                    "\nReceived mouseclick at time ",
                    str(datetime.datetime.fromtimestamp(time.time())),
                )
                turn_role = "speaker"
                turn_role_text = turn_speak_text
                turn_pass_mic_text = "When you want to pass the mic, click your mouse"
                show_role.setText(turn_role_text, log=True)
                show_pass.setText(turn_pass_mic_text, log=False)
                # log audio file pointer positions at turn changes
                # hdr: fLog.write('condition,role,time.time,run.time,comm.time,audio_position\n')
                fLog.write(
                    str(COND)
                    + ","
                    + turn_role
                    + ","
                    + str(timeThere)
                    + ","
                    + str(runThere)
                    + ","
                    + str(commThere)
                    + ","
                    + str(fOut.tell())
                    + "\n"
                )
                # start audio input / stop audio output
                queueInput.put("start")
                queueOutput.put("stop")
            if incoming == b"esc":
                cleanup(queueInput, queueOutput, audioInput, audioOutput, fLog, fTTL)
                return

        # update screen
        show_timer.setText(
            str(round(comm_time - comm_clock.getTime())) + " seconds", log=False
        )

        # refresh the screen
        win.flip()

    ## -- blank screen --
    show_prompt.setAutoDraw(False)
    show_role.setAutoDraw(False)
    show_timer.setAutoDraw(False)
    show_pass.setAutoDraw(False)
    show_lines.setAutoDraw(False)
    show_blank.draw()
    win.flip()

    # stop audio input and output
    queueInput.put("stop")
    queueOutput.put("stop")
    # log audio file pointer positions at start of trial
    # hdr: fLog.write('condition,role,time.time,run.time,comm.time,audio_position\n')
    fLog.write(
        str(COND)
        + ",trial_end,"
        + str(time.time())
        + ","
        + str(conv_clock.getTime())
        + ","
        + str(comm_clock.getTime())
        + ","
        + str(fOut.tell())
        + "\n"
    )

    # duration between trials
    blank_clock.reset()
    while blank_clock.getTime() < between_time:
        keys = event.getKeys(key_list)
        if keys:
            # if event.getKeys returns a '=', its a TTL
            if keys[0] == "5":
                # hdr: fTTL.write('condition,role,segment,time.time,run.time,comm.time\n')
                fTTL.write(
                    ",,"
                    + "blank"
                    + ","
                    + str(time.time())
                    + ","
                    + str(conv_clock.getTime())
                    + ",,\n"
                )
            # escape quits
            elif keys[0] == "escape":
                cleanup(queueInput, queueOutput, audioInput, audioOutput, fLog, fTTL)
                return

    ## -- save responses --
    thisExp.addData("condition", str(COND))
    thisExp.addData("first_speaker", first_speaker[0])
    thisExp.addData("onset", turn_onset)
    thisExp.addData("rt_time", turn_rt_time)
    thisExp.addData("rt_run", turn_rt_run)
    thisExp.addData("rt_comm", turn_rt_comm)
    thisExp.nextEntry()

    # -- End Scan Script --

    # draw end screen
    show_end.draw()
    win.flip()

    # close audio
    queueInput.put("die")
    queueOutput.put("die")
    audioInput.join()
    audioOutput.join()
    fOut.close()

    # save csv (these shouldn't be strictly necessary, should auto-save)
    thisExp.saveAsWideText(filename + ".csv")
    thisExp.saveAsPickle(filename)
    thisExp.abort()
    logging.flush()

    # close log files
    fLog.close()
    fTTL.close()

    # Total duration
    total_duration = global_clock.getTime()
    print("Total duration: " + str(total_duration / 60) + " minutes")

    # keep screen up until experimenter presses escape
    while True:
        keys = event.getKeys(key_list)
        if keys:
            if keys[0] == "escape":
                break

    win.close()
    core.quit()
    return


# %% MAIN
if __name__ == "__main__":
    # input arguments
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-dyad",
        "--DYAD",
        nargs="?",
        type=str,
        required=True,
        default="",
        help="Specify dyad #",
    )
    parser.add_argument(
        "-subj",
        "--SUBJ",
        nargs="?",
        type=int,
        required=True,
        help="Specify subject letter",
    )
    parser.add_argument(
        "-cond",
        "--COND",
        nargs="?",
        type=int,
        required=True,
        help="Specify condition (0 or 1)",
    )
    parser.add_argument(
        "-n",
        "--NAT",
        nargs="?",
        type=int,
        default=1,
        help="Flag for local NAT: set to 0 if ports are forwarded through "
        + "NAT, set to 1 otherwise. If 1, provide IP as well! Default = 1",
    )
    parser.add_argument(
        "-c",
        "--CHUNK",
        nargs="?",
        type=int,
        default=512,
        help="Audio chunk (packet) size in frames (1 frame = 2 bytes with "
        + "current format settings). Integer. Default = 512",
    )
    parser.add_argument(
        "-b",
        "--BUFFER",
        nargs="?",
        type=int,
        default=4,
        help="No. of chunks to buffer for audio output. Integer. Default = 4",
    )
    parser.add_argument(
        "-s",
        "--STUN",
        nargs="?",
        type=int,
        default=0,
        help="Flag to run stunclient (1) or not (0) "
        + "at the beginning of the script. Requires installed "
        + "stunclient. Default = 0",
    )
    parser.add_argument(
        "-l",
        "--LOGTTL",
        nargs="?",
        type=int,
        default=0,
        help="Flag for logging scanner ttl signals and their timestamps: "
        + "0 means no ttl log (for testing outside scanner), 1 means "
        + "ttl log. Default = 1",
    )
    args = parser.parse_args()

    # check inputs
    if 0 <= args.NAT <= 1:
        pass
    else:
        raise ValueError("Unexpected value for argument NAT ", "(should be 0 or 1)")
    # the following check for power of two is a really cool trick!
    if (
        (args.CHUNK != 0)
        and ((args.CHUNK & (args.CHUNK - 1)) == 0)
        and (not (args.CHUNK < 128))
        and (not (args.CHUNK > 4096))
    ):
        pass
    else:
        raise ValueError("CHUNK should be power of two, between 128 and 4096.")
    if 1 <= args.BUFFER <= 25:
        pass
    else:
        raise ValueError(
            "Unexpected value for argument BUFFER. ", "(please have it 1 <= and <= 25."
        )
    if 0 <= args.STUN <= 1:
        pass
    else:
        raise ValueError("Unexpected value for argument STUN ", "(should be 0 or 1)")
    if 0 <= args.COND <= 1:
        pass
    else:
        raise ValueError("Unexpected value for argument COND ", "(should be 0 or 1)")

    global BUFFER, CHUNK, DYAD, SUBJ, COND
    BUFFER = args.BUFFER
    CHUNK = args.CHUNK
    DYAD = args.DYAD
    SUBJ = args.SUBJ
    COND = args.COND

    # Run experiment (function goGo)
    goGo(args.NAT, args.STUN, args.LOGTTL, args.DYAD, args.SUBJ, args.COND)
    # End
    print("\n\nEverything ended / closed the way we expected. Goodbye!\n\n")
