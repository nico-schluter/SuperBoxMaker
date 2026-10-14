# Here you define the commands that will be added to your add-in.
# To add an additional command, create a directory next to this file containing
# an "entry" module with start()/stop() functions, then import it here.
from .createBox import entry as createBox

# Fusion will automatically call the start() and stop() functions of each.
commands = [
    createBox
]


# Assumes you defined a "start" function in each of your modules.
# The start function will be run when the add-in is started.
def start():
    for command in commands:
        command.start()


# Assumes you defined a "stop" function in each of your modules.
# The stop function will be run when the add-in is stopped.
def stop():
    for command in commands:
        command.stop()
