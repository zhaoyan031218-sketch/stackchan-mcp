#pragma once

#include <stddef.h>
#include <stdint.h>

using esp_err_t = int;
constexpr esp_err_t ESP_OK = 0;
constexpr int LEDC_TIMER_0 = 0;
constexpr int LEDC_CHANNEL_0 = 0;
constexpr int PIXFORMAT_RGB565 = 2;
constexpr int FRAMESIZE_QVGA = 5;
constexpr int CAMERA_FB_IN_PSRAM = 0;
constexpr int CAMERA_GRAB_WHEN_EMPTY = 0;

struct camera_config_t {
    int pin_pwdn, pin_reset, pin_xclk, pin_sccb_sda, pin_sccb_scl;
    int pin_d0, pin_d1, pin_d2, pin_d3, pin_d4, pin_d5, pin_d6, pin_d7;
    int pin_vsync, pin_href, pin_pclk;
    int xclk_freq_hz, ledc_timer, ledc_channel;
    int pixel_format, frame_size, jpeg_quality;
    size_t fb_count;
    int fb_location, grab_mode;
};

struct camera_fb_t { uint8_t marker; };

esp_err_t esp_camera_init(const camera_config_t* config);
esp_err_t esp_camera_deinit();
camera_fb_t* esp_camera_fb_get();
void esp_camera_fb_return(camera_fb_t* fb);
