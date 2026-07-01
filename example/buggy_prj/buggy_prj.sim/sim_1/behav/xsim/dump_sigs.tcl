log_wave -r /
run 500ns
set f [open "sigs.csv" w]
puts $f "time,signal,value"
for {set t 0} {$t < 500} {set t [expr {$t + 25}]} {
    seek_wave ${t}ns
    set sigs [get_objects -r /tb_buggy/u_dut/*]
    foreach s $sigs {
        set name [get_property NAME $s]
        set width [get_property WIDTH $s]
        set val [get_value $s]
        puts $f "$t,$name,$val"
    }
}
close $f
puts "===DUMP_DONE==="
