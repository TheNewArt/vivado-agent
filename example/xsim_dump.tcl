log_wave -r /
run 500ns
set fptr [open "signal_dump.csv" w]
puts \ "time,clk,rst_n,count"
for {set t 0} {\ < 500} {set t [expr {\ + 10}]} {
    seek_wave \ns
    set clk_val [read_wave_value [get_objects -r */clk]]
    set rst_val [read_wave_value [get_objects -r */rst_n]]
    set cnt_val [read_wave_value [get_objects -r */count]]
    puts \ "\,\,\,\"
}
close \
puts "===DUMP_DONE==="
