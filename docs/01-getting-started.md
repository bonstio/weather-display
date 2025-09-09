# Getting Started

This guide will walk you through setting up your own miniature weather display. The project uses a Raspberry Pi and a small OLED screens to show live weather data from the OpenWeatherMap API.

## Hardware Requirements ⚙️

The hardware for this project remains the same as the original Train Departure Display.

- Raspberry Pi (Zero, Zero 2, 3A+, 3B+, 4)
- 2.42" 256x64 SPI OLED Display Module (SSD1322)
- Micro SD Card (8GB minimum)
- Power Supply for your Raspberry Pi
- Jumper wires (female-female)
- Optional: 3D printed case (files available in the `case/` directory)
- Optional: A second OLED display for dual-screen mode

## Software Requirements 🖥️

1.  **balenaCloud Account**: We'll use balenaCloud to deploy the software to the Raspberry Pi. It makes setup and configuration much easier. [Sign up for a free account here](https://dashboard.balena-cloud.com/register).
2.  **OpenWeatherMap API Key**: The display fetches data from the OpenWeatherMap "One Call" API. You'll need to sign up for a free account and get an API key.
    * Go to [https://openweathermap.org/](https://openweathermap.org/) and create an account.
    * Navigate to the "API keys" tab in your user dashboard.
    * Copy the key. You'll need it during the configuration step. **Note:** It can take up to an hour for a new API key to become active.

## Installation

Once you have the free account set up, running this project is as simple as deploying it to a balenaCloud fleet. The basic premise is that you add a fleet, add your device (or devices - you can have more than one device running the same code!), and then deploy software. You can do it in just a few clicks by using the button below which will automatically guide you through adding your first fleet and deploying the code.

[![balena deploy button](https://balena.io/deploy.svg)](https://dashboard.balena-cloud.com/deploy?repoUrl=https://github.com/bonstio/weather-display&defaultDeviceType=raspberry-pi)

Once your SD card has been flashed, set it aside as we'll need it later after we have assembled the hardware.

To update at a later date, simply return to this page and click the above button again to deploy the latest code to your existing fleet. Your device will automatically update over-the-air!

**Alternatively**, sign up, add a fleet and device as per the [getting started](https://www.balena.io/docs/learn/getting-started/raspberrypi3/python/) guide. Then use the [balena CLI](https://github.com/balena-io/balena-cli) to push the project to your Pi.

This allows you to easily deploy multiple devices and configure them from the dashboard with the following variables.
**Next Step**: [Connecting the display to the Pi &rarr;](/docs/02-connecting-the-display-to-the-pi.md)