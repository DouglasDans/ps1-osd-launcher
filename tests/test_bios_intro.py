from src.bios_intro import AudioFeeder

CHUNK = 100


class FakeChannel:
    def __init__(self):
        self.playing = None
        self.queued = None
        self.log = []

    def get_busy(self):
        return self.playing is not None

    def get_queue(self):
        return self.queued

    def play(self, sound):
        self.playing = sound
        self.log.append(("play", sound))

    def queue(self, sound):
        self.queued = sound
        self.log.append(("queue", sound))


def _chunk(tag: int, samples: int = CHUNK) -> bytes:
    return bytes([tag]) * (samples * 4)


def _feeder(chunks):
    channel = FakeChannel()
    source = list(chunks)
    feeder = AudioFeeder(channel, lambda: source.pop(0) if source else None, sound=lambda b: b)
    return feeder, channel


def test_first_chunk_plays_immediately_at_the_start():
    feeder, channel = _feeder([_chunk(1), _chunk(2)])

    feeder.pump(now_sample=0)

    assert channel.log[0] == ("play", _chunk(1))
    assert channel.log[1] == ("queue", _chunk(2))


def test_late_chunk_is_trimmed_to_stay_in_sync():
    feeder, channel = _feeder([_chunk(1)])

    feeder.pump(now_sample=30)

    assert channel.log == [("play", _chunk(1, CHUNK - 30))]


def test_chunks_already_in_the_past_are_dropped():
    feeder, channel = _feeder([_chunk(1), _chunk(2), _chunk(3)])

    feeder.pump(now_sample=2 * CHUNK + 10)

    assert channel.log == [("play", _chunk(3, CHUNK - 10))]


def test_while_playing_chunks_are_queued_back_to_back():
    feeder, channel = _feeder([_chunk(1), _chunk(2), _chunk(3)])
    feeder.pump(now_sample=0)

    channel.queued = None  # mixer moved chunk 2 to "playing"
    feeder.pump(now_sample=150)  # no realignment while busy

    assert channel.log[-1] == ("queue", _chunk(3))


def test_end_of_stream_is_reported():
    feeder, _ = _feeder([_chunk(1)])
    feeder.pump(now_sample=0)

    feeder.pump(now_sample=CHUNK)

    assert feeder.finished
