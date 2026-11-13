"""
AURORA Wake Engine: hold a key, say the wake phrase, and AURORA starts.

    keys.py       when a key counts as "held" (pure logic, no Windows calls)
    hook.py       the Windows keyboard hook that feeds it
    detector.py   recognises the wake phrase in the audio captured while the key is held
    launcher.py   wakes the running Command Centre, or starts it
    helper.py     the small background program that ties them together

The microphone is open only while the activation key is held; nothing is
recorded to disk and nothing is heard at any other time.
"""
