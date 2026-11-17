#pragma once

#include "esp_camera.h"

bool frame2jpg(camera_fb_t* fb, uint8_t quality, uint8_t** out, size_t* len);
