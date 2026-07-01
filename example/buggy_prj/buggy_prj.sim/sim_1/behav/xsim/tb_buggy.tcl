log_wave -r /
run 500ns
set f [open \"signal_dump.csv\" w]
puts \ \"time_ns,signal_name,value\"
# Dump all logged signals
foreach sig [get_objects -r /*] {
    set val [read_wave_value \]
    set name [get_property NAME \]
    puts \ \"500,\,\\"
}
close \
puts \"===DUMP_DONE===\"
