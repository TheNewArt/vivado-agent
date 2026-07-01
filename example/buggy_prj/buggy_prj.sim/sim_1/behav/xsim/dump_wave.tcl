log_wave -r /
run 500ns
set f [open "signal_dump.csv" w]
puts $f "time,signal,value"
foreach path {/tb_buggy/u_dut/* /tb_buggy/*} {
    foreach obj [get_objects -r $path] {
        set name [get_property NAME $obj]
        set val [examine $obj]
        puts $f "500,$name,$val"
    }
}
close $f
puts "===DUMP_DONE==="
