local z13_refresh_rates = {}
for refresh = 48, 180 do
    table.insert(z13_refresh_rates, refresh)
end

gamescope.config.known_displays.rogflowz13_lcd = {
    pretty_name = "ASUS ROG Flow Z13 TL134ADXP03",
    dynamic_refresh_rates = z13_refresh_rates,
    -- EDID advertises 48-180 Hz Adaptive-Sync and gives the same 869.04 MHz
    -- native timing at 60 and 180 Hz. Keep that pixel clock fixed and lengthen
    -- only the vertical front porch, matching the Steam Deck OLED/ROG Ally
    -- Gamescope modegen contract. Physical acceptance still needs device test.
    dynamic_modegen = function(base_mode, refresh)
        if refresh < 48 or refresh > 180 or refresh ~= math.floor(refresh) then
            warn("Unsupported refresh " .. refresh .. "Hz for ASUS ROG Flow Z13 LCD")
            return base_mode
        end

        local mode = {}
        for key, value in pairs(base_mode) do
            mode[key] = value
        end

        gamescope.modegen.set_resolution(mode, 2560, 1600)
        gamescope.modegen.set_h_timings(mode, 48, 32, 80)
        gamescope.modegen.set_v_timings(mode, 6, 6, 163)

        local pixel_clock = 869040
        local htotal = mode.htotal
        -- Round the total down so quantization never generates a mode below
        -- the requested minimum (notably at 48 Hz).
        local vtotal = math.floor((pixel_clock * 1000) / (htotal * refresh))
        local vfront_porch = vtotal - mode.vdisplay - 6 - 163
        if vfront_porch < 6 then
            warn("Invalid front porch for " .. refresh .. "Hz ASUS ROG Flow Z13 mode")
            return base_mode
        end

        gamescope.modegen.set_v_timings(mode, vfront_porch, 6, 163)
        mode.clock = pixel_clock
        mode.vrefresh = gamescope.modegen.calc_vrefresh(mode)
        return mode
    end,
    matches = function(display)
        if display.vendor == "TMA"
            and display.product == 0x0803
            and display.model == "TL134ADXP03" then
            return 5000
        end
        return -1
    end,
}

debug("Registered ASUS ROG Flow Z13 TL134ADXP03 as a known display")
