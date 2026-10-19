# RedZone Board for Google TV

One full-screen web view that opens the board at the address in
`app/src/main/res/values/strings.xml` (`board_url`, tradepi on the home network). No libraries.
The TV draws the page itself, so it's sharp and on time; the horn and voice play with no click.
The remote's arrows switch matchups, OK turns sound on if the TV blocked it, Back exits.

## Build (Mac)

    brew install --cask android-commandlinetools android-platform-tools
    echo "sdk.dir=/usr/local/share/android-commandlinetools" > local.properties
    ./gradlew assembleDebug

## Install on the TV

1. On the TV: Settings → System → About → tap "Android TV OS build" 7 times (developer mode).
2. Settings → System → Developer options → turn on USB debugging (it also allows network debugging).
3. Find the TV's IP: Settings → Network & Internet → your network.
4. From the Mac (accept the prompt on the TV the first time):

       adb connect <tv-ip>:5555
       adb install -r app/build/outputs/apk/debug/app-debug.apk

The app shows up as "RedZone Board" in the TV's apps row.
