#!/usr/bin/env bash

./lazyusf --playback "$HOME/shared music/Game music/N64/Diddy Kong Racing/02 Title Theme.miniusf" 2>&1 |
awk '
  /Time \[min\]:/ { waiting_for_value = 1; next }
  waiting_for_value {
    printf "\rTime [min]: %s      ", $1
    fflush()
    waiting_for_value = 0
    next
  }
  { print; fflush() }
'

