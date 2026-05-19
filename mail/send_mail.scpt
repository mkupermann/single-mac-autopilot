-- send_mail.scpt
-- Sends a plain-text mail via Apple Mail. Used by the EQMOD autopilot watchdog
-- because macOS /usr/bin/mail does not work without sendmail configuration,
-- while Apple Mail is already set up on this user's machine.
--
-- Usage:
--     osascript send_mail.scpt <recipient> <subject> <body>
--
-- The body is passed as a single argument; embed newlines as literal \n in the
-- caller and this script will decode them.

on replaceText(theText, searchString, replacementString)
    set AppleScript's text item delimiters to searchString
    set theTextItems to text items of theText
    set AppleScript's text item delimiters to replacementString
    set theText to theTextItems as string
    set AppleScript's text item delimiters to ""
    return theText
end replaceText

on run argv
    if (count of argv) < 3 then
        return "ERROR: need <recipient> <subject> <body>"
    end if

    set recipientAddr to item 1 of argv
    set subj to item 2 of argv
    set bodyRaw to item 3 of argv

    -- decode \n -> newline
    set bodyText to my replaceText(bodyRaw, "\\n", linefeed)

    tell application "Mail"
        set newMsg to make new outgoing message with properties {subject:subj, content:bodyText, visible:false}
        tell newMsg
            make new to recipient at end of to recipients with properties {address:recipientAddr}
        end tell
        send newMsg
    end tell

    return "OK"
end run
