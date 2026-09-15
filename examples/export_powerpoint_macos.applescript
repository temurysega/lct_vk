-- Manual acceptance export. Arguments: generated.pptx output.pdf
-- Use an absolute output path and a new output filename.
-- Does not overwrite the source PPTX or close other presentations.
on run argv
    set inputFile to POSIX file (item 1 of argv)
    set outputFile to POSIX file (item 2 of argv)
    set deckName to name of (info for inputFile)
    set outputExists to false
    try
        info for outputFile
        set outputExists to true
    end try
    if outputExists then error "Output file already exists; choose a new filename"
    tell application "Microsoft PowerPoint"
        if exists presentation deckName then error "Close this presentation before exporting it with this script"
        open inputFile
        set deck to active presentation
        if name of deck is not deckName then error "Unexpected active presentation"
        try
            save deck in outputFile as save as PDF
        on error messageText number errorNumber
            close deck saving no
            error messageText number errorNumber
        end try
        close deck saving no
    end tell
end run
