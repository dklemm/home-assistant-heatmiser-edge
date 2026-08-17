![Heatmiser][image1]

# EDGE Series MODBUS Communication Protocol V1.8

Heatmiser UK LTD  
Units 8 & 9 Hurstwood Court  
Shadsworth Business Park  
Mercer Way  
Blackburn  
Lancashire BB1 2QU

## General

1. UART Baud Rate: 9600
2. UART Parity: None
3. Communications ID range: 0-32, 255
   - The MODBUS is invalid when Communications ID = 0.
   - The MODBUS is Radio command when Communications ID = 255.
4. Command = 03: Multiple register read command
5. Command = 06: Singel register write command
6. Command = 16: Multiple register write command
7. NOTE: Each send a packet of data, register number cannot exceed 60.

The read and write data interval greater than 50 ms.

## EDGE Heat Thermostat

| Command | Register address | Name of parameter | Values range | Default value | Note |
| --- | --- | --- | --- | --- | --- |
| 03 | 1 | Code version number | 10~255 |  |  |
| 03 | 2 | Relay status | 0~0xffff |  |  |
| 03 | 3 | Room temperature | 0~0xffff |  | Value = Temperature x 10 |
| 03 | 4 | Floor temperature | 0~0xffff |  | Value = Temperature x 10 |
| 03 | 5 | Remote sensor temperature | 0~0xffff |  | Value = Temperature x 10 |
| 03 | 6 | Window status | 0~0xffff |  |  |
| 03 | 7 | Current setting temperature | 5~35°C (41~95°F) |  | Value = Settemperature x 10 |
| 03 | 8 | Thermostat On/Off mode | 0:off, 1:on |  |  |
| 03 | 9 | Current operation mode | 0:change over, 1:schedule, 2:hold, 3:advanced, 4:away, 5:frost mode |  |  |
| 03 | 10 | Current schedule | 0:none, 1:Period1, 2:Period2, 3:Period3, 4:Period4, 5:Period5, 6:Period6 |  |  |
| 03 | 11 | Next schedule | 0:none, 1:Period1, 2:Period2, 3:Period3, 4:Period4, 5:Period5, 6:Period6 |  |  |
| 03 | 12 | Daylight saving status | 0: Cancel daylight saving time, 1: Run daylight savings time |  |  |
| 03 | 13 | Rate Of Change Information Only |  |  |  |
| 03 | 14 | Reserved |  |  |  |
| 03 | 15 | Before compensation Board sensor temperature | 0~0xffff |  | Value = Temperature x 10 |
| 03 | 16 | After compensation Board sensor temperature | 0~0xffff |  | Value = Temperature x 10 |
| 03 | 17 | Reserved |  |  |  |
| 03 | 18 | Reserved |  |  |  |
| 03 | 19 | Reserved |  |  |  |
| 03 | 20 | Reserved |  |  |  |
| 03/06/16 | 21 | Temp Format | 00 = °C, 01 = °F | 0 | 0 |
| 03/06/16 | 22 | Switching Differential | 0.5, 01, 02, 03 °C (01,02,04,06°F) | 1 °C (2°F) | Value = Settemperature x 10 |
| 03/06/16 | 23 | Output Delay | 00 – 15 minutes | 0 |  |
| 03/06/16 | 24 | Up/Down Limit | 00 – 10°C (0-18°F) | 0 | Value = Settemperature x 10 |
| 03/06/16 | 25 | Sensor Selection | 00 = ‘Built in and Remote Air’. (formally option 02), 01 = ‘Remote Air Sensors Only’. 02 = ‘Remote Floor Only’, 03 = ‘Floor Sensor & Built in Sensor & Remote Air Sensors’. 04 = ‘Floor Sensor & Remote Sensors Only’. | 0 |  |
| 03/06/16 | 26 | Floor Limit Temperature | 20°C – 45°C (68-113°F) | 28°C (85°F) | Value = Settemperature x 10 |
| 03/06/16 | 27 | Optimum Start | 00 = Disabled, 01 = 1 Hour, 02 = 2 Hour, 03 = 3 Hour, 04 = 4 Hour, 05 = 5 Hour | 0 |  |
| 03/06/16 | 28 | Program Type | 00 = 4 Period, 01 = 6 Period | 0 |  |
| 03/06/16 | 29 | Program Mode | 00 = 5/2, 01 = 7 Day, 02 = 24 Hour, 03 = None programmable | 0 |  |
| 03/06/16 | 30 | (DST) Daylight Saving | 00 = Disabled, 01 = Enabled |  |  |
| 03/06/16 | 31 | Communications ID (MODBUS) | 01-32, 00 = Disabled | 0 |  |
| 03/06/16 | 32 | Thermostat On/Off mode | 0:off, 1:on |  |  |
| 03/06/16 | 33 | Current operation mode | 0:change over, 1:schedule, 2:hold, 3:advanced, 4:away, 5:frost mode | 1 |  |
| 03/06/16 | 34 | Over right and Hold Set temperature | 5~35°C (41~95°F) |  | Value = Settemperature x 10 |
| 03/06/16 | 35 | Advanced Set temperature | 5~35°C (41~95°F) |  | Value = Settemperature x 10 |
| 03/06/16 | 36 | Reserved |  |  |  |
| 03/06/16 | 37 | FrostSet temperature | 7~17°C (45~63°F) |  | Value = Settemperature x 10 |
| 03/06/16 | 38 | Holdtime Hour+minute | high 8bit hour: (0-99) low 8bit minute: (0-59) | 0 |  |
| 03/06/16 | 39 | Awaytime Hour+minute | high 8bit hour: (0-23) low 8bit minute: (0-59) | 0 |  |
| 03/06/16 | 40 | Awaytime Month+Day | high 8bit Month: (1-12) low 8bit Day: (0-31) | 0 |  |
| 03/06/16 | 41 | Awaytime Year | Year: (2000~5000) | 0 |  |
| 03/06/16 | 42 | KeyLock PassWord | (0~9999) | 0 | Cancel Keylock (Value = 0) General PassWord: 6343 |
| 03/06/16 | 43 | TPI | 00 = Off, 01 = 3 cycles per hour, 02 = 6cycles per hour, 03 = 12 cycles per hour | 0 |  |
| 03/06/16 | 44 | TPI minimum On time | 00~05minute | 1 |  |
| 03/06/16 | 45 | Reserved |  | 0 |  |
| 03/06/16 | 46 | Restore the factory Settings | 0: normal, 1: Restore factory Settings | 0 |  |
| 03/06/16 | 47 | Synchronous RTC Year | Year: (2000~5000) | 0xffff | The value automatic assignment 0xffff when after the success of the RTC synchronization |
| 03/06/16 | 48 | Synchronous RTC Month+Day | high 8bit Month: (1-12) low 8bit Day: (1-31) | 0xffff | The value automatic assignment 0xffff when after the success of the RTC synchronization |
| 03/06/16 | 49 | Synchronous RTC Hour+minute | high 8bit hour: (0-23) low 8bit minute: (0-59) | 0xffff | The value automatic assignment 0xffff when after the success of the RTC synchronization |
| 03/06/16 | 50 | Synchronous RTC second | second:(0-59) | 0xffff | The value automatic assignment 0xffff when after the success of the RTC synchronization |
| 03/06/16 | 51 | Sunday Period1 Hour | 0-24 | 9 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 52 | Sunday Period1 Minute | 0-59 | 0 |  |
| 03/06/16 | 53 | Sunday Period1 SetTemp | 5~35°C (41~95°F) | 21°C (70°F) | Value = Settemperature x 10 |
| 03/06/16 | 54 | Reserved | 0 | 0 |  |
| 03/06/16 | 55 | Sunday Period2 Hour | 0-24 | 22 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 56 | Sunday Period2 Minute | 0-59 | 0 |  |
| 03/06/16 | 57 | Sunday Period2 SetTemp | 5~35°C (41~95°F) | 16°C (61°F) | Value = Settemperature x 10 |
| 03/06/16 | 58 | Reserved | 0 | 0 |  |
| 03/06/16 | 59 | Sunday Period3 Hour | 0-24 | 24 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 60 | Sunday Period3 Minute | 0-59 | 0 |  |
| 03/06/16 | 61 | Sunday Period3 SetTemp | 5~35°C (41~95°F) | 21°C (70°F) | Value = Settemperature x 10 |
| 03/06/16 | 62 | Reserved | 0 | 0 |  |
| 03/06/16 | 63 | Sunday Period4 Hour | 0-24 | 24 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 64 | Sunday Period4 Minute | 0-59 | 0 |  |
| 03/06/16 | 65 | Sunday Period4 SetTemp | 5~35°C (41~95°F) | 16°C (61°F) | Value = Settemperature x 10 |
| 03/06/16 | 66 | Reserved | 0 | 0 |  |
| 03/06/16 | 67 | Sunday Period5 Hour | 0-24 | 24 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 68 | Sunday Period5 Minute | 0-59 | 0 |  |
| 03/06/16 | 69 | Sunday Period5 SetTemp | 5~35°C (41~95°F) | 21°C (70°F) | Value = Settemperature x 10 |
| 03/06/16 | 70 | Reserved | 0 | 0 |  |
| 03/06/16 | 71 | Sunday Period6 Hour | 0-24 | 24 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 72 | Sunday Period6 Minute | 0-59 | 0 |  |
| 03/06/16 | 73 | Sunday Period6 SetTemp | 5~35°C (41~95°F) | 16°C (61°F) | Value = Settemperature x 10 |
| 03/06/16 | 74 | Reserved | 0 | 0 |  |
| 03/06/16 | 75 | Monday Period1 Hour | 0-24 | 7 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 76 | Monday Period1 Minute | 0-59 | 0 |  |
| 03/06/16 | 77 | Monday Period1 SetTemp | 5~35°C (41~95°F) | 21°C (70°F) | Value = Settemperature x 10 |
| 03/06/16 | 78 | Reserved | 0 | 0 |  |
| 03/06/16 | 79 | Monday Period2 Hour | 0-24 | 9 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 80 | Monday Period2 Minute | 0-59 | 0 |  |
| 03/06/16 | 81 | Monday Period2 SetTemp | 5~35°C (41~95°F) | 16°C (61°F) | Value = Settemperature x 10 |
| 03/06/16 | 82 | Reserved | 0 | 0 |  |
| 03/06/16 | 83 | Monday Period3 Hour | 0-24 | 16 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 84 | Monday Period3 Minute | 0-59 | 0 |  |
| 03/06/16 | 85 | Monday Period3 SetTemp | 5~35°C (41~95°F) | 21°C (70°F) | Value = Settemperature x 10 |
| 03/06/16 | 86 | Reserved | 0 | 0 |  |
| 03/06/16 | 87 | Monday Period4 Hour | 0-24 | 22 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 88 | Monday Period4 Minute | 0-59 | 0 |  |
| 03/06/16 | 89 | Monday Period4 SetTemp | 5~35°C (41~95°F) | 16°C (61°F) | Value = Settemperature x 10 |
| 03/06/16 | 90 | Reserved | 0 | 0 |  |
| 03/06/16 | 91 | Monday Period5 Hour | 0-24 | 24 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 92 | Monday Period5 Minute | 0-59 | 0 |  |
| 03/06/16 | 93 | Monday Period5 SetTemp | 5~35°C (41~95°F) | 21°C (70°F) | Value = Settemperature x 10 |
| 03/06/16 | 94 | Reserved | 0 | 0 |  |
| 03/06/16 | 95 | Monday Period6 Hour | 0-24 | 24 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 96 | Monday Period6 Minute | 0-59 | 0 |  |
| 03/06/16 | 97 | Monday Period6 SetTemp | 5~35°C (41~95°F) | 16°C (61°F) | Value = Settemperature x 10 |
| 03/06/16 | 98 | Reserved | 0 | 0 |  |
| 03/06/16 | 99 | Tuesday Period1 Hour | 0-24 | 7 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 100 | Tuesday Period1 Minute | 0-59 | 0 |  |
| 03/06/16 | 101 | Tuesday Period1 SetTemp | 5~35°C (41~95°F) | 21°C (70°F) | Value = Settemperature x 10 |
| 03/06/16 | 102 | Reserved | 0 | 0 |  |
| 03/06/16 | 103 | Tuesday Period2 Hour | 0-24 | 9 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 104 | Tuesday Period2 Minute | 0-59 | 0 |  |
| 03/06/16 | 105 | Tuesday Period2 SetTemp | 5~35°C (41~95°F) | 16°C (61°F) | Value = Settemperature x 10 |
| 03/06/16 | 106 | Reserved | 0 | 0 |  |
| 03/06/16 | 107 | Tuesday Period3 Hour | 0-24 | 16 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 108 | Tuesday Period3 Minute | 0-59 | 0 |  |
| 03/06/16 | 109 | Tuesday Period3 SetTemp | 5~35°C (41~95°F) | 21°C (70°F) | Value = Settemperature x 10 |
| 03/06/16 | 110 | Reserved | 0 | 0 |  |
| 03/06/16 | 111 | Tuesday Period4 Hour | 0-24 | 22 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 112 | Tuesday Period4 Minute | 0-59 | 0 |  |
| 03/06/16 | 113 | Tuesday Period4 SetTemp | 5~35°C (41~95°F) | 16°C (61°F) | Value = Settemperature x 10 |
| 03/06/16 | 114 | Reserved | 0 | 0 |  |
| 03/06/16 | 115 | Tuesday Period5 Hour | 0-24 | 24 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 116 | Tuesday Period5 Minute | 0-59 | 0 |  |
| 03/06/16 | 117 | Tuesday Period5 SetTemp | 5~35°C (41~95°F) | 21°C (70°F) | Value = Settemperature x 10 |
| 03/06/16 | 118 | Reserved | 0 | 0 |  |
| 03/06/16 | 119 | Tuesday Period6 Hour | 0-24 | 24 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 120 | Tuesday Period6 Minute | 0-59 | 0 |  |
| 03/06/16 | 121 | Tuesday Period6 SetTemp | 5~35°C (41~95°F) | 16°C (61°F) | Value = Settemperature x 10 |
| 03/06/16 | 122 | Reserved | 0 | 0 |  |
| 03/06/16 | 123 | Wednesday Period1 Hour | 0-24 | 7 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 124 | Wednesday Period1 Minute | 0-59 | 0 |  |
| 03/06/16 | 125 | Wednesday Period1 SetTemp | 5~35°C (41~95°F) | 21°C (70°F) | Value = Settemperature x 10 |
| 03/06/16 | 126 | Reserved | 0 | 0 |  |
| 03/06/16 | 127 | Wednesday Period2 Hour | 0-24 | 9 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 128 | Wednesday Period2 Minute | 0-59 | 0 |  |
| 03/06/16 | 129 | Wednesday Period2 SetTemp | 5~35°C (41~95°F) | 16°C (61°F) | Value = Settemperature x 10 |
| 03/06/16 | 130 | Reserved | 0 | 0 |  |
| 03/06/16 | 131 | Wednesday Period3 Hour | 0-24 | 16 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 132 | Wednesday Period3 Minute | 0-59 | 0 |  |
| 03/06/16 | 133 | Wednesday Period3 SetTemp | 5~35°C (41~95°F) | 21°C (70°F) | Value = Settemperature x 10 |
| 03/06/16 | 134 | Reserved | 0 | 0 |  |
| 03/06/16 | 135 | Wednesday Period4 Hour | 0-24 | 22 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 136 | Wednesday Period4 Minute | 0-59 | 0 |  |
| 03/06/16 | 137 | Wednesday Period4 SetTemp | 5~35°C (41~95°F) | 16°C (61°F) | Value = Settemperature x 10 |
| 03/06/16 | 138 | Reserved | 0 | 0 |  |
| 03/06/16 | 139 | Wednesday Period5 Hour | 0-24 | 24 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 140 | Wednesday Period5 Minute | 0-59 | 0 |  |
| 03/06/16 | 141 | Wednesday Period5 SetTemp | 5~35°C (41~95°F) | 21°C (70°F) | Value = Settemperature x 10 |
| 03/06/16 | 142 | Reserved | 0 | 0 |  |
| 03/06/16 | 143 | Wednesday Period6 Hour | 0-24 | 24 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 144 | Wednesday Period6 Minute | 0-59 | 0 |  |
| 03/06/16 | 145 | Wednesday Period6 SetTemp | 5~35°C (41~95°F) | 16°C (61°F) | Value = Settemperature x 10 |
| 03/06/16 | 146 | Reserved | 0 | 0 |  |
| 03/06/16 | 147 | Thursday Period1 Hour | 0-24 | 7 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 148 | Thursday Period1 Minute | 0-59 | 0 |  |
| 03/06/16 | 149 | Thursday Period1 SetTemp | 5~35°C (41~95°F) | 21°C (70°F) | Value = Settemperature x 10 |
| 03/06/16 | 150 | Reserved | 0 | 0 |  |
| 03/06/16 | 151 | Thursday Period2 Hour | 0-24 | 9 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 152 | Thursday Period2 Minute | 0-59 | 0 |  |
| 03/06/16 | 153 | Thursday Period2 SetTemp | 5~35°C (41~95°F) | 16°C (61°F) | Value = Settemperature x 10 |
| 03/06/16 | 154 | Reserved | 0 | 0 |  |
| 03/06/16 | 155 | Thursday Period3 Hour | 0-24 | 16 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 156 | Thursday Period3 Minute | 0-59 | 0 |  |
| 03/06/16 | 157 | Thursday Period3 SetTemp | 5~35°C (41~95°F) | 21°C (70°F) | Value = Settemperature x 10 |
| 03/06/16 | 158 | Reserved | 0 | 0 |  |
| 03/06/16 | 159 | Thursday Period4 Hour | 0-24 | 22 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 160 | Thursday Period4 Minute | 0-59 | 0 |  |
| 03/06/16 | 161 | Thursday Period4 SetTemp | 5~35°C (41~95°F) | 16°C (61°F) | Value = Settemperature x 10 |
| 03/06/16 | 162 | Reserved | 0 | 0 |  |
| 03/06/16 | 163 | Thursday Period5 Hour | 0-24 | 24 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 164 | Thursday Period5 Minute | 0-59 | 0 |  |
| 03/06/16 | 165 | Thursday Period5 SetTemp | 5~35°C (41~95°F) | 21°C (70°F) | Value = Settemperature x 10 |
| 03/06/16 | 166 | Reserved | 0 | 0 |  |
| 03/06/16 | 167 | Thursday Period6 Hour | 0-24 | 24 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 168 | Thursday Period6 Minute | 0-59 | 0 |  |
| 03/06/16 | 169 | Thursday Period6 SetTemp | 5~35°C (41~95°F) | 16°C (61°F) | Value = Settemperature x 10 |
| 03/06/16 | 170 | Reserved | 0 | 0 |  |
| 03/06/16 | 171 | Friday Period1 Hour | 0-24 | 7 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 172 | Friday Period1 Minute | 0-59 | 0 |  |
| 03/06/16 | 173 | Friday Period1 SetTemp | 5~35°C (41~95°F) | 21°C (70°F) | Value = Settemperature x 10 |
| 03/06/16 | 174 | Reserved | 0 | 0 |  |
| 03/06/16 | 175 | Friday Period2 Hour | 0-24 | 9 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 176 | Friday Period2 Minute | 0-59 | 0 |  |
| 03/06/16 | 177 | Friday Period2 SetTemp | 5~35°C (41~95°F) | 16°C (61°F) | Value = Settemperature x 10 |
| 03/06/16 | 178 | Reserved | 0 | 0 |  |
| 03/06/16 | 179 | Friday Period3 Hour | 0-24 | 16 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 180 | Friday Period3 Minute | 0-59 | 0 |  |
| 03/06/16 | 181 | Friday Period3 SetTemp | 5~35°C (41~95°F) | 21°C (70°F) | Value = Settemperature x 10 |
| 03/06/16 | 182 | Reserved | 0 | 0 |  |
| 03/06/16 | 183 | Friday Period4 Hour | 0-24 | 22 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 184 | Friday Period4 Minute | 0-59 | 0 |  |
| 03/06/16 | 185 | Friday Period4 SetTemp | 5~35°C (41~95°F) | 16°C (61°F) | Value = Settemperature x 10 |
| 03/06/16 | 186 | Reserved | 0 | 0 |  |
| 03/06/16 | 187 | Friday Period5 Hour | 0-24 | 24 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 188 | Friday Period5 Minute | 0-59 | 0 |  |
| 03/06/16 | 189 | Friday Period5 SetTemp | 5~35°C (41~95°F) | 21°C (70°F) | Value = Settemperature x 10 |
| 03/06/16 | 190 | Reserved | 0 | 0 |  |
| 03/06/16 | 191 | Friday Period6 Hour | 0-24 | 24 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 192 | Friday Period6 Minute | 0-59 | 0 |  |
| 03/06/16 | 193 | Friday Period6 SetTemp | 5~35°C (41~95°F) | 16°C (61°F) | Value = Settemperature x 10 |
| 03/06/16 | 194 | Reserved | 0 | 0 |  |
| 03/06/16 | 195 | Saturday Period1 Hour | 0-24 | 9 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 196 | Saturday Period1 Minute | 0-59 | 0 |  |
| 03/06/16 | 197 | Saturday Period1 SetTemp | 5~35°C (41~95°F) | 21°C (70°F) | Value = Settemperature x 10 |
| 03/06/16 | 198 | Reserved | 0 | 0 |  |
| 03/06/16 | 199 | Saturday Period2 Hour | 0-24 | 22 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 200 | Saturday Period2 Minute | 0-59 | 0 |  |
| 03/06/16 | 201 | Saturday Period2 SetTemp | 5~35°C (41~95°F) | 16°C (61°F) | Value = Settemperature x 10 |
| 03/06/16 | 202 | Reserved | 0 | 0 |  |
| 03/06/16 | 203 | Saturday Period3 Hour | 0-24 | 24 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 204 | Saturday Period3 Minute | 0-59 | 0 |  |
| 03/06/16 | 205 | Saturday Period3 SetTemp | 5~35°C (41~95°F) | 21°C (70°F) | Value = Settemperature x 10 |
| 03/06/16 | 206 | Reserved | 0 | 0 |  |
| 03/06/16 | 207 | Saturday Period4 Hour | 0-24 | 24 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 208 | Saturday Period4 Minute | 0-59 | 0 |  |
| 03/06/16 | 209 | Saturday Period4 SetTemp | 5~35°C (41~95°F) | 16°C (61°F) | Value = Settemperature x 10 |
| 03/06/16 | 210 | Reserved | 0 | 0 |  |
| 03/06/16 | 211 | Saturday Period5 Hour | 0-24 | 24 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 212 | Saturday Period5 Minute | 0-59 | 0 |  |
| 03/06/16 | 213 | Saturday Period5 SetTemp | 5~35°C (41~95°F) | 21°C (70°F) | Value = Settemperature x 10 |
| 03/06/16 | 214 | Reserved | 0 | 0 |  |
| 03/06/16 | 215 | Saturday Period6 Hour | 0-24 | 24 | The current schedule is invalid when the hour =24. |
| 03/06/16 | 216 | Saturday Period6 Minute | 0-59 | 0 |  |
| 03/06/16 | 217 | Saturday Period6 SetTemp | 5~35°C (41~95°F) | 16°C (61°F) | Value = Settemperature x 10 |
| 03/06/16 | 218 | Reserved | 0 | 0 |  |

## EDGE Timer Thermostat

| Command | Register address | Name of parameter | Values range | Default value | Note |
| --- | --- | --- | --- | --- | --- |
| 03 | 1 | Code version number | 10~255 |  |  |
| 03 | 2 | Relay status | 0~0xffff |  |  |
| 03 | 3 | Thermostat On/Off mode | 0:off, 1:on |  |  |
| 03 | 4 | Current schedule | 0:none, 1:Period1, 2:Period2, 3:Period3, 4:Period4 |  |  |
| 03 | 5 | Next schedule | 0:none, 1:Period1, 2:Period2, 3:Period3, 4:Period4 |  |  |
| 03 | 6 | Daylight saving status | 0: Cancel daylight saving time, 1: Run daylight savings time |  |  |
| 03 | 7 | Reserved |  |  |  |
| 03 | 8 | Reserved |  |  |  |
| 03 | 9 | Current operation mode | 0:change over, 1:schedule, 2:hold, 3:advanced, 4:away, 5:Standby mode |  |  |
| 03 | 10 | Reserved |  |  |  |
| 03 | 11 | Reserved |  |  |  |
| 03 | 12 | Reserved |  |  |  |
| 03 | 13 | Reserved |  |  |  |
| 03 | 14 | Reserved |  |  |  |
| 03 | 15 | Reserved |  |  |  |
| 03 | 16 | Reserved |  |  |  |
| 03 | 17 | Reserved |  |  |  |
| 03 | 18 | Reserved |  |  |  |
| 03 | 19 | Reserved |  |  |  |
| 03 | 20 | Reserved |  |  |  |
| 03/06/16 | 21 | Reserved |  |  |  |
| 03/06/16 | 22 | Reserved |  |  |  |
| 03/06/16 | 23 | Reserved |  |  |  |
| 03/06/16 | 24 | Reserved |  |  |  |
| 03/06/16 | 25 | Reserved |  |  |  |
| 03/06/16 | 26 | Reserved |  |  |  |
| 03/06/16 | 27 | Reserved |  |  |  |
| 03/06/16 | 28 | Reserved |  |  |  |
| 03/06/16 | 29 | Program Mode | 00 = 5/2, 01 = 7 Day, 02 = 24 Hour, 03 = None programmable | 0 |  |
| 03/06/16 | 30 | (DST) Daylight Saving | 00 = Disabled, 01 = Enabled |  |  |
| 03/06/16 | 31 | Communications ID (MODBUS) | 01-32, 00 = Disabled | 0 |  |
| 03/06/16 | 32 | Thermostat On/Off mode | 0:off, 1:on |  |  |
| 03/06/16 | 33 | Current operation mode | 0:change over, 1:schedule, 2:hold, 3:advanced, 4:away, 5:Standby mode | 1 |  |
| 03/06/16 | 34 | Timer Out force | 0: off, 1: on | 0 | In the Hold and Advanced mode |
| 03/06/16 | 35 | Reserved |  |  |  |
| 03/06/16 | 36 | Reserved |  |  |  |
| 03/06/16 | 37 | Reserved |  |  |  |
| 03/06/16 | 38 | Holdtime Hour+minute | high 8bit hour: (0-99) low 8bit minute: (0-59) | 0 |  |
| 03/06/16 | 39 | Awaytime Hour+minute | high 8bit hour: (0-23) low 8bit minute: (0-59) | 0 |  |
| 03/06/16 | 40 | Awaytime Month+Day | high 8bit Month: (1-12) low 8bit Day: (0-31) | 0 |  |
| 03/06/16 | 41 | Awaytime Year | Year: (2000~5000) | 0 |  |
| 03/06/16 | 42 | Reserved |  | 0 |  |
| 03/06/16 | 43 | Reserved |  | 0 |  |
| 03/06/16 | 44 | Reserved |  | 0 |  |
| 03/06/16 | 45 | Reserved |  | 0 |  |
| 03/06/16 | 46 | Restore the factory Settings | 0: normal, 1: Restore factory Settings | 0 |  |
| 03/06/16 | 47 | Synchronous RTC Year | Year: (2000~5000) | 0xffff | The value automatic assignment 0xffff when after the success of the RTC synchronization |
| 03/06/16 | 48 | Synchronous RTC Month+Day | high 8bit Month: (1-12) low 8bit Day: (1-31) | 0xffff | The value automatic assignment 0xffff when after the success of the RTC synchronization |
| 03/06/16 | 49 | Synchronous RTC Hour+minute | high 8bit hour: (0-23) low 8bit minute: (0-59) | 0xffff | The value automatic assignment 0xffff when after the success of the RTC synchronization |
| 03/06/16 | 50 | Synchronous RTC second | second:(0-59) | 0xffff | The value automatic assignment 0xffff when after the success of the RTC synchronization |
| 03/06/16 | 51 | Sunday Period1 On Hour | 0-24 | 7 | The current timer is invalid when the hour =24. |
| 03/06/16 | 52 | Sunday Period1 On MIN | 0-59 | 0 |  |
| 03/06/16 | 53 | Sunday Period1 Off Hour | 0-24 | 9 | The current timer is invalid when the hour =24. |
| 03/06/16 | 54 | Sunday Period1 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 55 | Sunday Period2 On Hour | 0-24 | 16 | The current timer is invalid when the hour =24. |
| 03/06/16 | 56 | Sunday Period2 On MIN | 0-59 | 0 |  |
| 03/06/16 | 57 | Sunday Period2 Off Hour | 0-24 | 20 | The current timer is invalid when the hour =24. |
| 03/06/16 | 58 | Sunday Period2 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 59 | Sunday Period3 On Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 60 | Sunday Period3 On MIN | 0-59 | 0 |  |
| 03/06/16 | 61 | Sunday Period3 Off Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 62 | Sunday Period3 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 63 | Sunday Period4 On Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 64 | Sunday Period4 On MIN | 0-59 | 0 |  |
| 03/06/16 | 65 | Sunday Period4 Off Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 66 | Sunday Period4 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 67 | Monday Period1 On Hour | 0-24 | 7 | The current timer is invalid when the hour =24. |
| 03/06/16 | 68 | Monday Period1 On MIN | 0-59 | 0 |  |
| 03/06/16 | 69 | Monday Period1 Off Hour | 0-24 | 9 | The current timer is invalid when the hour =24. |
| 03/06/16 | 70 | Monday Period1 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 71 | Monday Period2 On Hour | 0-24 | 16 | The current timer is invalid when the hour =24. |
| 03/06/16 | 72 | Monday Period2 On MIN | 0-59 | 0 |  |
| 03/06/16 | 73 | Monday Period2 Off Hour | 0-24 | 20 | The current timer is invalid when the hour =24. |
| 03/06/16 | 74 | Monday Period2 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 75 | Monday Period3 On Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 76 | Monday Period3 On MIN | 0-59 | 0 |  |
| 03/06/16 | 77 | Monday Period3 Off Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 78 | Monday Period3 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 79 | Monday Period4 On Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 80 | Monday Period4 On MIN | 0-59 | 0 |  |
| 03/06/16 | 81 | Monday Period4 Off Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 82 | Monday Period4 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 83 | Tuesday Period1 On Hour | 0-24 | 7 | The current timer is invalid when the hour =24. |
| 03/06/16 | 84 | Tuesday Period1 On MIN | 0-59 | 0 |  |
| 03/06/16 | 85 | Tuesday Period1 Off Hour | 0-24 | 9 | The current timer is invalid when the hour =24. |
| 03/06/16 | 86 | Tuesday Period1 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 87 | Tuesday Period2 On Hour | 0-24 | 16 | The current timer is invalid when the hour =24. |
| 03/06/16 | 88 | Tuesday Period2 On MIN | 0-59 | 0 |  |
| 03/06/16 | 89 | Tuesday Period2 Off Hour | 0-24 | 20 | The current timer is invalid when the hour =24. |
| 03/06/16 | 90 | Tuesday Period2 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 91 | Tuesday Period3 On Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 92 | Tuesday Period3 On MIN | 0-59 | 0 |  |
| 03/06/16 | 93 | Tuesday Period3 Off Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 94 | Tuesday Period3 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 95 | Tuesday Period4 On Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 96 | Tuesday Period4 On MIN | 0-59 | 0 |  |
| 03/06/16 | 97 | Tuesday Period4 Off Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 98 | Tuesday Period4 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 99 | Wednesday Period1 On Hour | 0-24 | 7 | The current timer is invalid when the hour =24. |
| 03/06/16 | 100 | Wednesday Period1 On MIN | 0-59 | 0 |  |
| 03/06/16 | 101 | Wednesday Period1 Off Hour | 0-24 | 9 | The current timer is invalid when the hour =24. |
| 03/06/16 | 102 | Wednesday Period1 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 103 | Wednesday Period2 On Hour | 0-24 | 16 | The current timer is invalid when the hour =24. |
| 03/06/16 | 104 | Wednesday Period2 On MIN | 0-59 | 0 |  |
| 03/06/16 | 105 | Wednesday Period2 Off Hour | 0-24 | 20 | The current timer is invalid when the hour =24. |
| 03/06/16 | 106 | Wednesday Period2 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 107 | Wednesday Period3 On Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 108 | Wednesday Period3 On MIN | 0-59 | 0 |  |
| 03/06/16 | 109 | Wednesday Period3 Off Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 110 | Wednesday Period3 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 111 | Wednesday Period4 On Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 112 | Wednesday Period4 On MIN | 0-59 | 0 |  |
| 03/06/16 | 113 | Wednesday Period4 Off Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 114 | Wednesday Period4 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 115 | Thursday Period1 On Hour | 0-24 | 7 | The current timer is invalid when the hour =24. |
| 03/06/16 | 116 | Thursday Period1 On MIN | 0-59 | 0 |  |
| 03/06/16 | 117 | Thursday Period1 Off Hour | 0-24 | 9 | The current timer is invalid when the hour =24. |
| 03/06/16 | 118 | Thursday Period1 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 119 | Thursday Period2 On Hour | 0-24 | 16 | The current timer is invalid when the hour =24. |
| 03/06/16 | 120 | Thursday Period2 On MIN | 0-59 | 0 |  |
| 03/06/16 | 121 | Thursday Period2 Off Hour | 0-24 | 20 | The current timer is invalid when the hour =24. |
| 03/06/16 | 122 | Thursday Period2 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 123 | Thursday Period3 On Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 124 | Thursday Period3 On MIN | 0-59 | 0 |  |
| 03/06/16 | 125 | Thursday Period3 Off Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 126 | Thursday Period3 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 127 | Thursday Period4 On Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 128 | Thursday Period4 On MIN | 0-59 | 0 |  |
| 03/06/16 | 129 | Thursday Period4 Off Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 130 | Thursday Period4 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 131 | Friday Period1 On Hour | 0-24 | 7 | The current timer is invalid when the hour =24. |
| 03/06/16 | 132 | Friday Period1 On MIN | 0-59 | 0 |  |
| 03/06/16 | 133 | Friday Period1 Off Hour | 0-24 | 9 | The current timer is invalid when the hour =24. |
| 03/06/16 | 134 | Friday Period1 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 135 | Friday Period2 On Hour | 0-24 | 16 | The current timer is invalid when the hour =24. |
| 03/06/16 | 136 | Friday Period2 On MIN | 0-59 | 0 |  |
| 03/06/16 | 137 | Friday Period2 Off Hour | 0-24 | 20 | The current timer is invalid when the hour =24. |
| 03/06/16 | 138 | Friday Period2 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 139 | Friday Period3 On Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 140 | Friday Period3 On MIN | 0-59 | 0 |  |
| 03/06/16 | 141 | Friday Period3 Off Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 142 | Friday Period3 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 143 | Friday Period4 On Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 144 | Friday Period4 On MIN | 0-59 | 0 |  |
| 03/06/16 | 145 | Friday Period4 Off Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 146 | Friday Period4 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 147 | Saturday Period1 On Hour | 0-24 | 7 | The current timer is invalid when the hour =24. |
| 03/06/16 | 148 | Saturday Period1 On MIN | 0-59 | 0 |  |
| 03/06/16 | 149 | Saturday Period1 Off Hour | 0-24 | 9 | The current timer is invalid when the hour =24. |
| 03/06/16 | 150 | Saturday Period1 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 151 | Saturday Period2 On Hour | 0-24 | 16 | The current timer is invalid when the hour =24. |
| 03/06/16 | 152 | Saturday Period2 On MIN | 0-59 | 0 |  |
| 03/06/16 | 153 | Saturday Period2 Off Hour | 0-24 | 20 | The current timer is invalid when the hour =24. |
| 03/06/16 | 154 | Saturday Period2 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 155 | Saturday Period3 On Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 156 | Saturday Period3 On MIN | 0-59 | 0 |  |
| 03/06/16 | 157 | Saturday Period3 Off Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 158 | Saturday Period3 Off MIN | 0-59 | 0 |  |
| 03/06/16 | 159 | Saturday Period4 On Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 160 | Saturday Period4 On MIN | 0-59 | 0 |  |
| 03/06/16 | 161 | Saturday Period4 Off Hour | 0-24 | 24 | The current timer is invalid when the hour =24. |
| 03/06/16 | 162 | Saturday Period4 Off MIN | 0-59 | 0 |  |

[image1]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAATkAAABDCAYAAADnL3LiAAAd0ElEQVR4Xu2dd3RUx73HnZfy3nnJOXnnPCwQYOMGmBrjiruNbdxi3PISt2Cb5iQuuAW3YIMd28QdiB2awUkwTQIEBgQWBgyYZkD0JtQAYUCIIgQqu6t58/3d+d2dO/fuakGURZo/Plrdu3dmfjPzm++duTN39ozq6mphsdQLIhFJmAhXh0VEfkaqQwqcx/dR6JyHgDgtSc8Z5gmLpa4SqZbCBfGShOX/1eEooUi1CEsiirC8PiSpktcBhDXjs5weWJGz1BusyNVPrMhZ6g80DA0RGKqGlOCBKhq+hmgYC0IYnobkd2EFwprxWU4LrMhZ6g3UgwuHiVCkSvbgwqJCngOHq0PisBS+ctmLAxXozUlxC+P5HZ7d4VleQJyW5MeKnKXeoIschqBrSg+LN5fmEs/MzRVvL88XaVu2ETNyC0VZRUhUSjEE1ZFKX3yW0wMrcpZ6gxW5+okVOUudAss+nOUgVTSBEJJiBiLVVfR5IOLwRnaRuGXy92JOcSlxSC0pCUVCRAThw9WiiqHJCAxfnQkKxFUdxjm1HCXAFktyYEXOUqcgIaJZVHlMggRhksehkDgov39kbg7x2IJ8sQ+TCaFqApMLYUw88PXo8UlhXLlnH/HP7C1iSckhUS6vAzQrS2k46+6q7exr0mJFzlKnQI8KYPkHLQcJOxySgtVzfq7okLGJ+CEUdoRNDV8jYfTcKkS4qoQ4vDtbHlfS5ATYLUXy9SX54p3sAqKCZmAhdFbkkh0rcpY6hRU5i4kVOUsdA2LjiA+esVXK/8GsXaXi5+NzxL/z9xMYjjpr49QaOBwfWC9K5/Vy2DBCSLVzh6+gVIrmo3O3EO+vLRJSJ2U8Dn47LMmCFTlLnSL6TM6ZeKiCeEkezMoXqel54oA8B/BMjRb5VjlUHVwnSr/pKipndiIOr/9MCltFVOTQI5Rqtq60nLhk0hqxuaxcVMl0gH0jInmxImepU/BwFUNUDFmLpUCBX07IF7/9FsPMEFGFmVQIYriUKF30gghl3iQqMzsTZYuel+J2hITNAb1DZ+EweGhhvnh3/W4SP0CTEAH2WE49VuQsdQp3+IlPORz9avte4j8n5InuC/Lk0DVEYLiKWdWKA+uI8pl3ivC0zqJq2u1E+cz75fB1Iw1paVhLw1uIY4R4acU2cdesDaJMihsw7bAkD1bkLHUKK3IWEytyljoF7yLirGMLi3H5xcTPxm8Xb60qcXcVcRbzRsSRgq+I0r91FkXXtxbbWrYgdj1wmSifP1gOWTH5gJlXiCdEzlkY/NKKHeLG6TnigIwH2MXAyYsVOUudAlskERFBbzmMK9hL/CQtX/RYkC8qZS8M4JkdxKss6wti+/mtxd5USeNWRHHjtqLwN7eJSNl+IqR6gJUyHOizolB0mJIndsveILAil7xYkbPULbShKkjL30P8NE0OL+cViiOyNwacnUhCYtennxC7G7cWxantpNC1JXY3bi+2t2gvyjdvJKpDGOLKnp/sHYKrZxaKy2ZsFful4AGfHZakwYqcpW5hRc5iYEXOUqfQd/7F56bSCuIXaXni5+O3irzyEBEJO6JVNPJLYmfTNlLk2rqUNGot8tp3FJXbdhBYRoL1cgv3HSH+Jz1f3DI9h17sB/aNh+TlpIscOSG9RuO8VuO85BxFqE92Vne2LCAuy7HhlqtaEIsH8Fzup3vZRxcDA/S60PuKiI6z8sSP03eIPit/ICqw9k0KXXnhNiLnkhuoJ1ecih5da7GrcVtR8PIbIlIZIvBMDm883Dknn/iP9G3i3TW73Tcq7DO55OUUiZxD40apolFKQx8Nz0wRC+bPJ07Hhpbs0C4bkq6/70qkyvLmcqeyX7DAeZ/zNCx7HqY62yZV0xbnYGROsfiJHLL+YlweMbZgnwiFMQmBPeNComzjFpH7Ql+xpdfzRMGgYaL6UCktGgaHZRyvZe8UP5tQSDRJzxNbDle4w2PTDkvyYEWuHmJFzopcfeKUiBz/QlKTho1EY9m4iDMVEDo0tG/nE2hsZhyW2oE6qCivEOc1O4eAyHG5U9lLkTvdxI2hX+TCkFW9isU31DL5/2/n5YofTdxO/Hd6jhi4cZfYL4ehAO+6huXwtbrSAbuSYD3c5vIQ0XVBgfjZ+G3ip2kFxHvr98jvscGmI6J2uJq8nHSRI1jkZE8uSORSJQvnLyBO18aWzKBMly5ZSuJGAqdEDuVOZb9g4Wlb7vxMjncD5ndPIUTbK8Lilpn5xI/Sd4ofp20XrabmEs+u2C3+lVcsRuftIYZv3SseXFgkGk7YSpwx8QfxX+NzxWurdxJH+Nkl0pDYiYfk5dSInOrixxI5DJ2syJ04UKYfffBhVORUHdQFkXP3d1M9Of61LdxU8XrWXnkOPLtsu/jl2E3ijPQi4j/SimQPrVD8JL3AQQrgj9LR6ysimqRtEQM3F4tyObIAjoDantzpgBW5eogVOSty9YmTLnLkDEcpcmaD43N0Hp947qKOafkApaERYEc89PiDcH7QxMG07XhgplcTZviaQHndf/c9p0zkgmw385RI+ub1Ds7wFH6hb3hJfqI+ne+qxIaySvHhhj3EQ3NzRceZ20WLjDzimqztouu3W8WILbuJwsoqmsjg8M6zPycdh2Cb4tlr5idRzHh0yCdxDXHsaRwNpg1BxLvePGfGX1tOqcg11UVO42hEjr4LY18wtY31cSgkM34dTud4pmfC6eh5N23Qj83wNRGqCrkCdypFzswHn0ukbGOFjxW3m656hiYli0SLRbAcM6xStMoiDuUR54enXaFMUDDM9M3vg+w6Gsx8clp6mR1r3IHonYWADoNpi0lQOQRdk0idHyunhcjphYxeyJxv5ohJEycR6RPSxGT5uXb1GoIWtwakmzBIR8axRsYFJk+aLAa8O0D06Nad6P30M+LL0V+K6dOmE0cOHwms/KPCcCTMfCLujMmTiUnpEymfE9PTCZyb/+1RLq/R4s+QeSJRM4TOI3IoR9PBdWLEHRPP9REqN9gxUeYNpKeliykZGWJvcTHhpm/mQ4HvV2VnU/2AiWlO2XwzezYRquKdQ6JlRA1KTUw4C4ZD7iQYbX5ZHf1JQvenBrkhxipnTz4jYvPGTUTG5AzKH9uH46yvs9zZ3mP2UxmurKyMmD5tmhj979Hi6aeeJrp36ybeG/A3ShesW7s2bhnGxKg7oTDP41qauT50iPhq6lTx6eC/ixHDhhMzpk8XYfVbGnr8KM/58+cTAwYMEFOmTLEipxesFTkrcsCKnBW5RElOkTsTi4EXEMj0odJD4j0pNKBl8xbexcOqkfIar9/cd7/YWbSzRgHQn6mhcCsrKohPBw0WF//qIt8CZR1dEHD8pyf+IHI2byE43Xjpm9fkbs0V/fq+Lq7qeCVBeQlI1+Tcs5sRLz7/gti1a5enQdIzI3WcLcUAAt1TcfONnXxlrovevV3u9lzP9H31NYJeydPydukll/hs4/oAqD9cv2PbduLVl1+J1psR5uwmTQk01qrKSk85oZ5GfT6SuOySS/1paum2b9OWbg5RUVGNDXYTYXqm1r/f60TU3lQitUFjSSOxatVKohqCaNQh7Nm7dy/x7l/fFm1btY7GY9rFtsnPro/8nkC96PmL5R/MxvUbRPfHHnd9jxdv62ngvN6OLu1wsRjy2T+IhIazUrwgVIDjdONTac7Omk0gDpTx+eecS5i2IP0Osi1t2rDRk+5keZPOysoiMjMzxYQJE8SIESOImHbVguQVObUYeO2ateLiizp4exzq02ycTJdf3yWOHDlCxCo0vdAhMlddfgVBcRvxx4PT5MY5bNgwjzOZ6XLauObw4cMEnMPMg5lOIA1SCFzfuuWFVFaA4tfyN2vmTKcBcNw1xB/LluuuvoZAb4Lypu7ol118Sdw4dsmbztLFS0T7tu0IaqQBdpjpviDFm0Wq9GCpuP+ee10RiVVGqSgPlAuua3CmGDdmLMF1Iv8S/GZE/zf6EY0byPDy+sYNGhCNUv5XxtFArFy1mjB7RIhr2bJl4uIOHQjTdtOuoPz1fuYZ1xeA6Zs4FwqFxEfvf0A0bQThjcYdL62g9K6/7nqxYwc2G4jtm7rIUTxqMpDiS3HaJovcqFGjaEF/rHxTXchzF7VrL7bLmxsoLy8XgwYNEpMmTSK+++47MXLkSBI+gPz6bKolySlykhFDhxEXtmjpOrWnMHWMCm2UkkJdZ8CVyRVrHhfk5TsNz43XqRyfs6hKdtNRx5S+alQAgjVcCp2eXpBD4RwLMQuQ3oDjwTbxjDSf73TDjQSGgnrjmTVzlmOvJgBmmZtlaKYHrrvqagJ1R3nSRU5rDK596v/BnwwUzc873ytQuF41Aj1POiiXr6ZMJZ7v/azvexNKj+JWdSKPz5I3HlBYUODUBc2KRm3v168/4cahyrRhinOcnb2KqObtlFS4kr0lnh4sN2gXzg8+FZx3LgcWOdNH3HNS3N+XPVo3Tqo7o5y0+DnuqG86ZdxEfgLq2cke8M6dOwlOSx9+ekTO9S+FSmOo7BWCc+Qowi139Z1ej1wX+H/AO+8SFVLkBg8eLDIyMoilS5eKRYsWUW8O6GJ/vEhKkdMrEceoHO6aU5dY/15dY4bt1vVRgtJCupoz0R0SSwIkV8hK1yvKTP+JHj3F+rXrRATPFtTzhalTpogrr+hIODZo9sMRZb4wFOHhiNmz4/+5J5ei8oUhGHj6yafoOSM915FMxac87i6HjMDs+bHN3Hgyp8/w5Bd2dH+8mzvsbCN7fWaZ6/nGcBXX8XPInt17EH957S8E5yfRnhx6VPqx5zuFnhc9jmuvvIo4K7VxYFjznBkPH7//t/d8z4ZA/379CH0JkxtWsloKHHDDqDx/9vdPPfFzY765003EhPHjxZ7de+iZFDi4/wDV5btvvyOuu+ZaAiLHNyJPT071Xsd9OcabJyVaLB6tZAcAz+TKj5QTCFMke2qvvPwyQfnQRbGBU2aPPPwIoYupWzZGT47EWcEid3H7XxH4P9E6+GOvJwikMyVjipg2bRqBfH/99ddi+PDhhBU5hRU5vzPh04qcPx4+tiKXYkXuZJGoyLnOL48feehhmn0DmTMyxeh//Vu0ubAVoRem7nS339KZqKrEe4V+kfvogw8Is0I4DjzMByRsWliGZwE7oLJ1+5Uj3XfPvUQVZvm0cLoteD4B+vTpQ0KkO7zHXrVqn53xS+nYpjPpZfYH6UxBcfFM4r13dfGVuV52NKutBJ2cXzVsPnbjS1TkgDz3cp+XCDg5hHjoP4aItq3bEEH1YMaDh9tvyaEloBnEyZNpEgM0bdwkUCz5+DF5w6Nf6DL88VhF7tqrr/HYBq6XwuUKDtWbKi8C5cll6vxfWFjo8w2wr2QfgckTPX7HxhRx4QXNiTXSLr0+KE03jYgY9PEnPhsJPEeTYJjoSz+OyJn1ceP1N4gxUoh5FcC92gJzx17HZvzvipxKC2mD0aNH03O5ILE/XiStyHW8/Api8XeLfBUJsmZ9TVBhGmFBsyZNCTgLpWuEx4N6wGH08Hh+hLsw4Mr3OYMSjAnjxgfezbjXuQ69QCOsJ54A28xz/IaFe04KEHo3Zr5Z5DDxQlP3ejr4VDbfU4PI8S4ksex06zNBkcONYGvO1mh4wolv/rxvCe6dxoqjhWzUy5d9HyC0DgNlg9Z7+2YcF0kbUCamPx6tyIVDIeKKyy73xI80H3/0saiYRVSdqWeAvrJT5WnWNRj1+eeEWaZsU1/ZmwZBoxRPPclyulJNpumwnzz84EPeslR16pl4MNOX4X59x51E2aEyT9poa7gR6WmxyOHGC8y2rJeFfnw8SUqRQwXwfnJB6o7C2Lolh8BDZbMS9Ios2bvXDQMQ14wZM9zv9TAM1sLpFRGv8A8cOCDamXdc5Yzg4w8/cuLgB94BcZhp+Xpy2t2Zufaqq73paXnGA2EWdxclSCCmyCkSfuMhQZEbP2as2/D18sTnwYMHiXO1LZ+C4piVOZPy7S79ofKM2pIjfaHZWWd76lWPo4kc7gbN3B2NyMHeyopKAo8VTBsvl+cO7NtPxGrM8eC6vvOOOwizTDm9nJwcoqa48d2ggQM9NupxNT/3PBpJeOKpQeRQtniUAiicnp60/c3+b4oe3bs74HFH9x70OWzIUKImm08EVuS0MIwVOStyXB5W5LxYkUuAxEXOWQzMwzRPHBFn6QfApo+mE4CoyJW4YZjevXvHbAwAq+f16808cD4I+f1tnW/1ORL//7vf/J9yBgczHoBFr/8c9QU9NwLUWFU5AIpLHiMukDltelyRw9Bpb7Ej7h5OlciNHecpT44bnxUVFcQ1Kj+x4lizajVdHxU55UuKKik8WIyrl4keR21FzrWZbjhh0dEYrvJyGNzwAIbP+dI/4/kQo5cL1rGlNmxImGUKbrj2OvIXwDdDX3waX8+cFdvX5f9z58zx2piAyM3OyiL4ZqPnw3ND5nrCp34uwOYTSXKKXErsrZa48gryCwhT5NyKVPhEThb07x9+xP3eU+mK+XPneXo+JkI/lvG92a+/+yyIGxjHddUVHUXFkXK3R0qOAMGT//MasIvatvPabthDtuITDUk1JkpLy4NJiSZyriAom2NOPCiOh8jpTJAiR9ca4ZEGixwe5JvhaMZS2bRmzRpfeB1M8LRr3cYfhwITE+aIACQqcm4Ylee77rgzGsYIp4fnmVTMqtJrh+yH8APtf+b7Zd/7REmnc6ebfP4YkzBeuSqj2X7AzyrZr5DGpIkTvcITiS9yyBO/Omf2ppOVeily58ieEle0Xuk+YUkEzWG410U24BPnGpwpDh446Iock5aW5knPY7sRp2/ZjGGzmXdgRc7L8Ra5kcNHOOWvbjxB4T1xqXq8/bbbxLKlywhT4HDjw4xzPJHT651QdgbBPuPxTTURwGC2m9sj5y+eyAErcjVgRc6KnGuXFTkrcieBeilymKwwBQNiBPjYjC8WPqfjsORQznGpFDndmTdv2uQZbvrSa9hI/PmFF+nFZn65GctZPv7gQ+ICc5o+wC4rcl6Om8gpsOnA7bfeJsUDYqJs1eqCfEp96uAcNosFaeMn+CYohkiRi1evx4LHV2ArRFktKn7xuefdtClvVuRqz6kSORcZH16k91xrOGJtHIxXhvMxxAwzsLojP/PkU55rgJ427q6e2UjOtzoeE7AY2MSKnJfjLXKIC/XKbxfwi+p8s0zEn5qmNpY9uqUekRk6ZKjvutrCZei+uaBsQu8OmyDw6ILyZkWu9pxqkaOenHR4z7WGQ2JLJ6zKj8XUOJjfY4+0CsyERdSDZpl+s6ZnRRuValhIl3cy2bJpM+Wb9z3jfHMc+/ftF7/SNxUIyL8VOS/HW+T0mxbYsnmzeL1vX3HBeecTpk+Z9lD88vyDDzzguZnVJHJ4DdH0RxPTJ3XfJCZnEHjNbN369a5fUd6syNUeK3JW5Fy7IlbkrMideOqfyEWcV7pM59OP8ZqR6cRuHKphx8IUFDOeXT/somduushx2njhGjjxOA7ETuT8j3MQyrCzrkzl0cw78Ikclblj08lYJ6dTl0XOPOb1YNgV9+4uXTx1xP7F53Dc8oLmIhQOEQiP/QhN39TBhqemT3lsC/BJP077o/DsU+pmiu+tyNWSUyVyulP06tnL40j6HRfMzMwU/FI8GoZpQzzYPl2g9PTxFkcsB9ZFzozXQ8R548EMr6OLnGkDXqQ2rweuyAWUeyD1WORiofsZ0sM29QC7TZv1juPzzm4minYUEQizdevWuLOr2C9Qn6VPqJ6OBitytedUixwc469vvhVX5J7845+OuxNx+thNlu/sJidS5PTG99ijj/quB6bIMb70XTscrMhF0f2M/le9pHlz5waKHB5PbNq0iUCY4uJi6t0BMw9sE2bnAfcaTRtqhRW52mNFzoqcbpMVOStyJ5p6KXLZK1Z6Fkiazoc4C2XcgPeTiyV6HofWrmP06wBeiMZyA4+tygYsUgZ5ubnu9b5GI/mhaKe4sHkLX551gkSObcLL06aw63Zg+yj3mY1hg25HfRK5oLLQy8SMl+HrtxUW+m5uKGuIWWVlJcHxdXvscSJWHT3/7HOEI3LR+nDrxUg7FqathBW52pMMIgfnuPXmW4ggkYP44UVoUMy7uwYIF8erf3dg/34xYvhwMWfOHIKv06/BnlseW5UNLLzYGVh3Rm5QeMEcvNTnpZgCzQSJHAP7YjUggF884zTjUR9FbuPGjURpaam3LAJsAvxC//AhQ311hWNsjMk9Mo5rTtZsIqh+9Tob8ulnnnA0e2/YYtbj8uXLxauvvkqYthJW5GqPLnJY9a3PMjJHI3LYosesBHIGhSlyFJ90DGzACNzXXTis0fjRcAYPHCRWrlhBQGSwaSL/BOHUKVPFwE8Gigd++zsCM6eIc9TIkQQ3bj39Z5562k1LXynPaSL8TZ06iWFDhxJYhvL5iM/drbXj9UKZeCIHu/U8BqV/3733ibFjxhD4XdQ/v/ii6Pb444RbjrrIGQKhk4jIXSNFzuzpeERu9ZrAOBjsAB0kchy+SZDIyfjcX+syRA5h4IerpMABzvM7b79DtGzRQrz+l77i27nziHCVMzvKvg3ge9idF/BiYb2csVAcAqvXDeCNOfGzhb561uob53ANLw0pKSmh8HjDBsydM5dewMfWYYB+xlGGOU/eZAHXn4sqE1fkAtomysWKXA0EiRz/mhDggowlckxBQQGRqMiZcK9q/Lhx7u4hrjNpjqQ7VCw816pzX3w+kjBFDmnycNmNA6/YBOTBhK9HgzlXDmvN73VMkdPB3R8/7xczDzFwf5KQ60Q1jstPoMhx48IOHkFxMPi9jlqJXEA4ErlVqwjn+gjtJgIoXrIv6i+YKb30og7E2TI9s3xNvzmnWbPAnwjk4/1yVHDzTTd73lQwfdOM08STL7WDDUY/ILA8A0ROb5uwwxU52GmGT0KsyFmR8+chBlbkrMhZkUsUzAiFg5/JsXOdaJFjRwLY1BHPbECQc5iOY6JfS0PJBmfSJpjAFDnmrf5vxozDzIOLuhZvZLzS56W4YeOKnEwfGzq2a9uWgM0UTjmyGReD52bAFLkT9UwOeeMGTsPVgLwwGK7SD+IgXIANgc/kNJELCoNz+uwq7GWRM2+MNcH5AfB7kDljhueZmV4uDISuy52/JoLq20zHTFOHbqzyE79hAoLqRBe5WGX5TdZsIlbbTDZOushR5amf90NPjkXNhHcGNh2Ayc/PJ86Vd0OzEqjyFbz9eSwofim4+bl5xAvPPkcvTpsOkyi33tKZNsLkn6ILTE+lOWrkKKKVegMjluOiQV1+6WW0awXADYJ6E+p7MwyO8UtiZtquDXiWIm3Ik/kFd3e52xeHGR8apTurpzVCcKkUOd1+jov/jyty5RUEv8Gh1x9vRApWr17tC68DkWvTqrV7PcfFzy8hcubOwEi/3+tvEOR3ms2wH8ersrMJziueT4KWzVu4W2B5euUG/D2R4uzuvHnjJsIsR90uHfYlzHpjA04zDdNvPHnQNgvAtvivvfIq9R6BWYbM3wcPJrgs3Z6kinO2FDhw3JevnCBOjcip2aQlixaLpYuXEEuYJUvE4sWL6Q4G+E6nx4Fz/Jul2MWB4wiCf5IwHrpNmAkrP3xETP9qGvHOX992f3+U4d8/BdgVGD+Ph2UdQJ/tMu32pKcRqgrRWxZwYvDhBx+KQQMHub+7umLFCl8Y9GKXyHICbhnK8gSLFy2iJQlmunr6PFyn8lUiz+80Yvv3ienpbvpwaPyuAZeR3vsAK+XwG3WnlzvZpuzZvWu3zwa2g2eM8XBf9wfOD3wBYDbTDK8Dm1YsX+76kekHS5cs9fXkuBypLI20Of1DMl3AedXL7buFC906e673sz4/6dWjp/jk408I+AhuuFE/cx7ZmPkIQk8b4Tau30CMGzNWPPWnJ33pMk/07CX+9cU/xdxv5hDw7aCeowmLIJe9WS77SkoI9nUzfLJhRc6wyYqcFTk9fSty/nKxIlcDeuM4GmLGYTS4IEwbTMzrgSsACE9UK5Cmnr5BAmmb13A6epq1wbU7IG1OPzCdGHkIut48rgnThqByiJU2cHfJiIEbPl4ejDj07/AIwLzeDGue82CkCz/Ry0ivYz2cmY8gfOkYcUZ904SFUaHHEydt87qaMMMnGydd5CwWi+VkYkXOYrHUaazIWSyWOo0VOYvFUqf5f6CzPTOyBYDBAAAAAElFTkSuQmCC>
