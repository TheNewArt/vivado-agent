log_wave -r /
run 500ns
set f [open "sigs.csv" w]
puts $f "time,signal,value"
# Dump at end of simulation (500ns)
set sigs [get_objects -r /tb_buggy/u_dut/*]
foreach s $sigs {
    set name [get_property NAME $s]
    set width [get_property WIDTH $s]
    set val [get_value $s]
    puts $f "500,$name,$val"
}
close $f
puts "===DUMP_DONE==="
