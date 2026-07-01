open_wave_database {buggy_prj/buggy_prj.sim/sim_1/behav/xsim/tb_buggy_behav.wdb}
create_wave_config
add_wave -r /
seek_wave 0ns
set fptr [open "signal_dump.txt" w]
for {set t 0} { < 500} {set t [expr { + 10}]} {
    seek_wave ns
    set sigs [list clk rst_n count]
    foreach sig \ {
        set val [read_wave_value \]
        puts \ "\,\,\"
    }
}
close \
puts "===DUMP_DONE==="
