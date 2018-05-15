#!/usr/bin/python2
""" Subscribe to NAO's mic audio

"""
from __future__ import print_function
import time
import sys
import Queue

from naoqi import ALProxy
from naoqi import ALBroker
from naoqi import ALModule
from optparse import OptionParser

from threading import Thread

from google.cloud import speech_v1
from google.oauth2 import service_account

from google.cloud.speech import enums
from google.cloud.speech import types
from google.rpc import code_pb2

NAO_IP = "192.168.0.102"

audioStreamer = None
audio_buffer = Queue.Queue()

class GoogleSTTClient(object):
    def __init__(self, sample_rate, credentials_json, recognized_cb):
        print('Initializing Google cloud speech service...')
        self.recognized_cb = recognized_cb
        self.sample_rate = int(sample_rate)
        creds = service_account.Credentials.from_service_account_file(credentials_json,
                                                                      scopes=['https://www.googleapis.com/auth/cloud-platform'])
        self.client = speech_v1.SpeechClient(credentials=creds)

        self.running = False
        self.buffer = Queue.Queue()
        self.restart_flag = False

        self.stream_thread = Thread(target=self.stream)
        self.stream_thread.start()

    def data_to_stream(self, data):
        if not self.running:
            return
        self.buffer.put(data, block=False)

    def stream(self):
        # Emulating a Do-while with while and break
        # this is to restart streaming if an error occured in listen print listen_print_loop
        while True:
            self.restart_flag = False
            config = types.RecognitionConfig(
                encoding=enums.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=self.sample_rate,
                language_code='en-US')
            streaming_config = types.StreamingRecognitionConfig(
                config=config,
                interim_results=True)
            self.requests = (types.StreamingRecognizeRequest(audio_content=content) for content in self.data_generator())

            responses = self.client.streaming_recognize(streaming_config, self.requests)
            print('Start talking...')
            self.running = True
            self.listen_print_loop(responses)

            if not self.restart_flag:
                break
            else:
                print("Restarting service...")


    def data_generator(self):
        """A generator that yields all available data in the given buffer.
        Yields:
            bytes: A chunk of data that is the aggregate of all chunks of data in
            `buff`. The function will block until at least one data chunk is
            available.
        """

        while self.running:
            # Use a blocking get() to ensure there's at least one chunk of data.
            chunk = self.buffer.get()
            if chunk is None:
                return
            data = [chunk]

            # Now consume whatever other data's still buffered.
            while self.running:
                try:
                    chunk = self.buffer.get(block=False)
                    if chunk is None:
                        self.running = False
                        return
                    data.append(chunk)
                except Queue.Empty:
                    break

            yield b''.join(data)

    def listen_print_loop(self, responses):
        """Iterates through server responses and prints them.

        The responses passed is a generator that will block until a response
        is provided by the server.

        Each response may contain multiple results, and each result may contain
        multiple alternatives; for details, see https://goo.gl/tjCPAU.  Here we
        print only the transcription for the top alternative of the top result.

        In this case, responses are provided for interim results as well. If the
        response is an interim one, print a line feed at the end of it, to allow
        the next result to overwrite it, until the response is a final one. For the
        final one, print a newline to preserve the finalized transcription.
        """
        num_chars_printed = 0
        # try:
        for response in responses:
            if not self.running:
                responses.cancel()
                return
            if response.error.code != code_pb2.OK:
                print ('Error: {} - {}'.format(response.error.code, response.error.message))
                responses.cancel()
                self.running = False
                self.restart_flag = True
                return
            if not response.results:
                continue

            # The `results` list is consecutive. For streaming, we only care about
            # the first result being considered, since once it's `is_final`, it
            # moves on to considering the next utterance.
            result = response.results[0]
            if not result.alternatives:
                continue

            # Display the transcription of the top alternative.
            transcript = result.alternatives[0].transcript

            # Display interim results, but with a carriage return at the end of the
            # line, so subsequent lines will overwrite them.
            #
            # If the previous result was longer than this one, we need to print
            # some extra spaces to overwrite the previous result
            overwrite_chars = ' ' * (num_chars_printed - len(transcript))

            if not result.is_final:
                sys.stdout.write(transcript + overwrite_chars + '\r')
                sys.stdout.flush()

                num_chars_printed = len(transcript)

            else:
                #print(transcript + overwrite_chars)
                self.recognized_cb(transcript, 'google')

                # Exit recognition if any of the transcribed phrases could be
                # one of our keywords.

                num_chars_printed = 0


    def isRunning(self):
        return self.running

    def shutdown(self):
        print ('Stopping Google STT Client...')
        self.running = False
        self.buffer.put(None)
        self.stream_thread.join()
        print ('Stopped Google STT Client')

class AudioStreamerModule(ALModule):
    def __init__(self, name, buffer):
        ALModule.__init__(self, name)
        # Create a proxy to ALAudioDevice
        self.audio_buffer = buffer
        try :
            self.audioDevice = ALProxy("ALAudioDevice")
        except Exception as e:
            print("Something wrong - {}".format(repr(e)))

    def start(self):
        rate = 48000
        channels = 3 # ALL_Channels: 0,  AL::LEFTCHANNEL: 1, AL::RIGHTCHANNEL: 2; AL::FRONTCHANNEL: 3  or AL::REARCHANNEL: 4.
        deinterleaved = 0
        self.audioDevice.setClientPreferences(self.getName(), rate, channels, deinterleaved)
        self.audioDevice.subscribe(self.getName())
        print ("Subscription set up!")

    def process(self, channels, samplesPerChannel, buffer, timestamp):
        # here buffer is just a pointer
        print ("process called,  {} channels with {} samples each - {}".format(channels, samplesPerChannel, len(buffer)))

    def processRemote(self, channels, samplesPerChannel, timestamp, buffer):
        self.audio_buffer.put(buffer, block=False)
        # print("process remote called, channels = {}, samples per channel = {}, sample len = {}, buffer len = {}...".format(channels, samplesPerChannel, len(buffer), self.audio_buffer.qsize()))

    def stop(self):
        """unsubscribe
        """
        self.audioDevice.unsubscribe(self.getName())
        print ("Unsubscribed")

def data_generator(buffer):
    """A generator that yields all available data in the given buffer forever.
    Yields:
        bytes: A chunk of data that is the aggregate of all chunks of data in
        `buffer`. The function will block until at least one data chunk is
        available.
    """
    while True:
        # Use a blocking get() to ensure there's at least one chunk of data.
        chunk = buffer.get()
        if chunk is None:
            return
        data = [chunk]

        # Now consume whatever other data is buffered.
        # data = []
        while True:
            try:
                chunk = buffer.get(block=False)
                if chunk is None:
                    return
                data.append(chunk)
            except Queue.Empty:
                break

        yield b''.join(data)

def transcription_cb(transcription):
    print('Got from Google:{}'.format(transcription))

def main():
    """ Main entry point

    """
    parser = OptionParser()
    parser.add_option("--pip",
        help="Parent broker port. The IP address or your robot",
        dest="pip")
    parser.add_option("--pport",
        help="Parent broker port. The port NAOqi is listening to",
        dest="pport",
        type="int")
    parser.set_defaults(
        pip=NAO_IP,
        pport=9559)

    # Start Google cloud stt
    credentials_json = '/home/cbarobotics/dev/catkin_ws/src/stt/credentials/google.json'
    stt = GoogleSTTClient(48000, credentials_json, transcription_cb)

    (opts, args_) = parser.parse_args()
    pip   = opts.pip
    pport = opts.pport

    # We need this broker to be able to construct
    # NAOqi modules and subscribe to other modules
    # The broker must stay alive until the program exits
    myBroker = ALBroker("myBroker",
       "0.0.0.0",   # listen to anyone
       0,           # find a free port and use it
       pip,         # parent broker IP
       pport)       # parent broker port

    # Warning: audioStreamer must be a global variable
    # The name given to the constructor must be the name of the
    # variable
    global audioStreamer, audio_buffer
    audioStreamer = AudioStreamerModule("audioStreamer", audio_buffer)
    audioStreamer.start()

    try:
        for raw_audio_data in data_generator(audio_buffer):
            # Do whatever you want with the data
            # print ("got data - {}, {} ...".format(len(raw_audio_data), raw_audio_data[:10]))
            stt.data_to_stream(raw_audio_data)

    except KeyboardInterrupt:
        print ("Interrupted by user, shutting down")
        audioStreamer.stop()
        myBroker.shutdown()
        stt.shutdown()
        exit()

if __name__ == "__main__":
    main()